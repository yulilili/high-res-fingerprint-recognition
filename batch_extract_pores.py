from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from six.moves import range

import tensorflow as tf
import numpy as np
import argparse
import os

import util
import pore_detector_descriptor

FLAGS = None


def main():
  print('Loading images...')
  images, image_names = util.load_images_with_names(FLAGS.imgs_dir)
  print('Done.')

  half_window_size = FLAGS.window_size // 2

  with tf.Graph().as_default():
    images_pl, _ = util.placeholder_inputs()

    print('Building graph...')
    net = pore_detector_descriptor.Net(
        images_pl, FLAGS.window_size, training=False)
    print('Done.')

    with tf.Session() as sess:
      print('Restoring model in {}...'.format(FLAGS.model_dir))
      util.restore_model(sess, FLAGS.model_dir)
      print('Done.')

      # extract pores for each image
      for i, img in enumerate(images):
        print('Extracting pores in image {}...'.format(image_names[i]))
        # predict probability of pores
        pred = sess.run(
            net.dets,
            feed_dict={images_pl: np.reshape(img, (1, ) + img.shape + (1, ))})

        # add borders lost in convolution
        pred = np.reshape(pred, pred.shape[1:-1])
        pred = np.pad(pred, ((half_window_size, half_window_size),
                             (half_window_size, half_window_size)), 'constant')

        # convert into coordinates
        pick = pred > 0.05
        coords = np.argwhere(pick)
        probs = pred[pick]

        # filter detections with nms
        dets, _ = util.nms(coords, probs, 9, 0.1)

        # save results
        filename = os.path.join(FLAGS.results_dir,
                                '{}.txt'.format(image_names[i]))
        util.save_dets_txt(dets, filename)


if __name__ == '__main__':
  parser = argparse.ArgumentParser()
  parser.add_argument(
      '--imgs_dir', required=True, type=str, help='Path to images directory')
  parser.add_argument(
      '--model_dir', type=str, required=True, help='Logging directory.')
  parser.add_argument(
      '--batch_size', type=int, default=256, help='Batch size.')
  parser.add_argument(
      '--window_size', type=int, default=17, help='Pore window size.')
  parser.add_argument(
      '--results_dir',
      type=str,
      default='result',
      help='Path to folder in which results should be saved.')
  FLAGS, unparsed = parser.parse_known_args()

  main()