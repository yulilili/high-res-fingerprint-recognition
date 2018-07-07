import numpy as np

import utils


def by_patches(sess, preds, batch_size, patches_pl, labels_pl, dataset):
  # initialize dataset statistics
  true_preds = []
  false_preds = []
  total = 0

  steps_per_epoch = (dataset.num_samples + batch_size - 1) // batch_size
  for _ in range(steps_per_epoch):
    feed_dict = utils.fill_feed_dict(dataset, patches_pl, labels_pl,
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

    # compute and update f score
    f_score = 2 * (tdr * (1 - fdr)) / (tdr + 1 - fdr)
    if f_score > best_f_score:
      best_tdr = tdr
      best_fdr = fdr
      best_f_score = f_score

  return best_f_score, best_fdr, best_tdr


def by_images(sess, pred_op, patches_pl, dataset):
  patch_size = dataset.patch_size
  half_patch_size = patch_size // 2
  preds = []
  pores = []
  print('Predicting pores...')
  for _ in range(dataset.num_images):
    # get next image and corresponding image label
    img, label = dataset.next_image_batch(1)
    img = img[0]
    label = label[0]

    # predict for each image
    pred = sess.run(
        pred_op,
        feed_dict={patches_pl: np.reshape(img, (-1, ) + img.shape + (1, ))})

    # put predictions in image format
    pred = np.array(pred).reshape(img.shape[0] - patch_size + 1,
                                  img.shape[1] - patch_size + 1)

    # add borders lost in convolution
    pred = np.pad(pred, ((half_patch_size, half_patch_size),
                         (half_patch_size, half_patch_size)), 'constant')

    # add image prediction to predictions
    preds.append(pred)

    # turn pore label image into list of pore coordinates
    pores.append(np.argwhere(label))
  print('Done.')

  # validate over thresholds
  inter_thrs = np.arange(0.7, 0, -0.1)
  prob_thrs = np.arange(0.9, 0, -0.1)
  best_f_score = 0
  best_tdr = None
  best_fdr = None
  best_inter_thr = None
  best_prob_thr = None

  # put inference in nms proper format
  for prob_thr in prob_thrs:
    coords = []
    probs = []
    for i in range(dataset.num_images):
      img_preds = preds[i]
      pick = img_preds > prob_thr
      coords.append(np.argwhere(pick))
      probs.append(img_preds[pick])

    for inter_thr in inter_thrs:
      # filter detections with nms
      dets = []
      for i in range(dataset.num_images):
        det, _ = utils.nms(coords[i], probs[i], 7, inter_thr)
        dets.append(det)

      # find correspondences between detections and pores
      total_pores = 0
      total_dets = 0
      true_dets = 0
      for i in range(dataset.num_images):
        # update totals
        total_pores += len(pores[i])
        total_dets += len(dets[i])

        # coincidences in pore-detection and detection-pore correspondences are true detections
        pore_corrs, det_corrs = utils.matmul_corr_finding(pores[i], dets[i])
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
        best_prob_thr = prob_thr

  return best_f_score, best_tdr, best_fdr, best_inter_thr, best_prob_thr


if __name__ == '__main__':
  import argparse
  import os
  import tensorflow as tf

  import polyu
  from models import detection

  parser = argparse.ArgumentParser()
  parser.add_argument(
      '--polyu_dir_path',
      required=True,
      type=str,
      help='Path to PolyU-HRF dataset')
  parser.add_argument(
      '--model_dir_path', type=str, required=True, help='Logging directory.')
  parser.add_argument(
      '--patch_size', type=int, default=17, help='Pore patch size.')
  flags = parser.parse_args()

  # load polyu dataset
  print('Loading PolyU-HRF dataset...')
  polyu_path = os.path.join(flags.polyu_dir_path, 'GroundTruth',
                            'PoreGroundTruth')
  dataset = polyu.detection.Dataset(
      os.path.join(polyu_path, 'PoreGroundTruthSampleimage'),
      os.path.join(polyu_path, 'PoreGroundTruthMarked'),
      split=(15, 5, 10),
      patch_size=flags.patch_size)
  print('Loaded.')

  # gets placeholders for patches and labels
  patches_pl, labels_pl = utils.placeholder_inputs()

  # builds inference graph
  net = detection.Net(patches_pl, training=False)

  with tf.Session() as sess:
    print('Restoring model...')
    utils.restore_model(sess, flags.model_dir_path)
    print('Done.')

    image_f_score, image_tdr, image_fdr, inter_thr, prob_thr = by_images(
        sess, net.predictions, patches_pl, dataset.val)
    print('Whole image evaluation:')
    print('TDR = {}'.format(image_tdr))
    print('FDR = {}'.format(image_fdr))
    print('F score = {}'.format(image_f_score))
    print('inter_thr = {}'.format(inter_thr))
    print('prob_thr = {}'.format(prob_thr))