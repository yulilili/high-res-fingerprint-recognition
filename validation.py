from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from six.moves import range

import numpy as np
import util


def by_windows(sess, preds, batch_size, windows_pl, labels_pl, dataset):
  # initialize dataset statistics
  true_preds = []
  false_preds = []
  total = 0

  steps_per_epoch = (dataset.num_samples + batch_size - 1) // batch_size
  for _ in range(steps_per_epoch):
    feed_dict = util.fill_window_feed_dict(dataset, windows_pl, labels_pl,
                                           batch_size)

    # evaluate batch
    batch_preds = sess.run(preds, feed_dict=feed_dict)
    batch_labels = feed_dict[labels_pl]
    batch_total = np.sum(batch_labels)

    # update dataset statistics
    total += batch_total
    if batch_total > 0:
      true_preds.extend(batch_preds[batch_labels == 1].flatten())
    if batch_total < batch_labels.shape[0]:
      false_preds.extend(batch_preds[batch_labels == 0].flatten())

  # sort for efficient computation of tdr/fdr over thresholds
  true_preds.sort()
  true_preds.reverse()
  false_preds.sort()
  false_preds.reverse()

  # compute tdr/fdr score over thresholds
  tdrs = []
  fdrs = []
  best_thr = 0
  best_f_score = 0
  best_fdr = None
  best_tdr = None

  true_pointer = 0
  false_pointer = 0

  eps = 1e-5
  thrs = np.arange(1.01, -0.01, -0.01)
  for thr in thrs:
    # compute true positives
    while true_pointer < len(true_preds) and true_preds[true_pointer] >= thr:
      true_pointer += 1

    # compute false positives
    while false_pointer < len(
        false_preds) and false_preds[false_pointer] >= thr:
      false_pointer += 1

    # compute tdr and fdr
    tdr = true_pointer / (total + eps)
    fdr = false_pointer / (true_pointer + false_pointer + eps)
    tdrs.append(tdr)
    fdrs.append(fdr)

    # compute and update f score
    f_score = 2 * (tdr * (1 - fdr)) / (tdr + 1 - fdr)
    if f_score > best_f_score:
      best_tdr = tdr
      best_fdr = fdr
      best_f_score = f_score
      best_thr = thr

  return np.array(tdrs, np.float32), np.array(
      fdrs, np.float32), best_f_score, best_fdr, best_tdr, best_thr


def by_images(sess, pred_op, batch_size, windows_pl, dataset):
  window_size = dataset.window_size
  half_window_size = window_size // 2
  preds = []
  pores = []
  print('Predicting pores...')
  for _ in range(dataset.num_images):
    # get next image and corresponding image label
    img, label = dataset.next_image_batch(1)
    img = img[0]
    label = label[0]

    # convert 'img' to windows
    windows = np.array(util.to_windows(img, window_size))

    # predict for each window
    pred = []
    for i in range((len(windows) + batch_size - 1) // batch_size):
      # sample batch
      batch_windows = windows[batch_size * i:batch_size * (i + 1)].reshape(
          [-1, window_size, window_size, 1])

      # predict for batch
      batch_preds = sess.run(pred_op, feed_dict={windows_pl: batch_windows})

      # update image pred
      pred.extend(batch_preds)

    # put predictions in image format
    pred = np.array(pred).reshape(img.shape[0] - window_size + 1,
                                  img.shape[1] - window_size + 1)

    # add borders lost in convolution
    pred = np.pad(pred, ((half_window_size, half_window_size),
                         (half_window_size, half_window_size)), 'constant')

    # add image prediction to predictions
    preds.append(pred)

    # turn pore label image into list of pore coordinates
    pores.append(np.argwhere(label))
  print('Done.')

  # put inference in nms proper format
  coords = []
  probs = []
  for i in range(dataset.num_images):
    img_preds = preds[i]
    pick = img_preds > 0.05
    coords.append(np.argwhere(pick))
    probs.append(img_preds[pick])

  # validate over thresholds
  inter_thrs = np.arange(0.7, 0, -0.1)
  dist_thrs = np.arange(5, 100)

  best_f_score = 0
  best_tdr = None
  best_fdr = None
  best_inter_thr = None
  best_dist_thr = None

  for dist_thr in dist_thrs:
    # initialize detections without filtering
    dets = coords[0:]
    det_probs = probs[0:]

    for inter_thr in inter_thrs:
      # further filter detections with nms
      for i in range(dataset.num_images):
        dets[i], det_probs[i] = util.nms(dets[i], det_probs[i], dist_thr,
                                         inter_thr)

      # find correspondences between detections and pores
      total_pores = 0
      total_dets = 0
      true_dets = 0
      for i in range(dataset.num_images):
        # update totals
        total_pores += len(pores[i])
        total_dets += len(dets[i])

        # coincidences in pore-detection and detection-pore correspondences are true detections
        pore_corrs, det_corrs = util.matmul_corr_finding(pores[i], dets[i])
        for pore_ind, pore_corr in enumerate(pore_corrs):
          if det_corrs[pore_corr] == pore_ind:
            true_dets += 1

      # compute tdr, fdr and f score
      eps = 1e-5
      tdr = true_dets / (total_pores + eps)
      fdr = (total_dets - true_dets) / (total_dets + eps)
      f_score = 2 * (tdr * (1 - fdr)) / (tdr + (1 - fdr))

      # update best parameters
      if f_score > best_f_score:
        best_f_score = f_score
        best_tdr = tdr
        best_fdr = fdr
        best_inter_thr = inter_thr
        best_dist_thr = dist_thr

  return best_f_score, best_tdr, best_fdr, best_inter_thr, best_dist_thr