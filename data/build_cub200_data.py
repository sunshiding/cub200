#!/usr/bin/python

# This file is an adaptation of the build_image_data.py file located here:
# https://github.com/tensorflow/models/blob/master/research/inception/inception/data/build_image_data.py
# A copyright below signifies the influence from tensorflow


# Copyright 2016 The TensorFlow Authors. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ==============================================================================

"""Converts image data to TFRecords file format with Example protos.
The image data set is expected to reside in JPEG files located in the
following directory structure.
  data_dir/label_0/image0.jpeg
  data_dir/label_0/image1.jpg
  ...
  data_dir/label_1/weird-image.jpeg
  data_dir/label_1/my-image.jpeg
  ...
where the sub-directory is the unique label associated with these images.
This TensorFlow script converts the training and evaluation data into
a sharded data set consisting of TFRecord files
  train_directory/train-00000-of-01024
  train_directory/train-00001-of-01024
  ...
  train_directory/train-01023-of-01024
and
  validation_directory/validation-00000-of-00128
  validation_directory/validation-00001-of-00128
  ...
  validation_directory/validation-00127-of-00128
where we have selected 1024 and 128 shards for each data set. Each record
within the TFRecord file is a serialized Example proto. The Example proto
contains the following fields:
  image/encoded: string containing JPEG encoded image in RGB colorspace
  image/height: integer, image height in pixels
  image/width: integer, image width in pixels
  image/colorspace: string, specifying the colorspace, always 'RGB'
  image/channels: integer, specifying the number of channels, always 3
  image/format: string, specifying the format, always 'JPEG'
  image/filename: string containing the basename of the image file
            e.g. 'n01440764_10026.JPEG' or 'ILSVRC2012_val_00000293.JPEG'
  image/class/label: integer specifying the index in a classification layer.
    The label ranges from [0, num_labels] where 0 is unused and left as
    the background class.
  image/class/text: string specifying the human-readable version of the label
    e.g. 'dog'
If your data set involves bounding boxes, please look at build_imagenet_data.py.
"""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

from datetime import datetime
import os
import random
import sys
import threading

import numpy as np
import tensorflow as tf

tf.app.flags.DEFINE_string('images_directory', '/tmp/', 'Images directory')
tf.app.flags.DEFINE_string('output_directory', '/tmp/', 'Output data directory')

tf.app.flags.DEFINE_integer('train_shards', 1024,
                            'Number of shards in training TFRecord files.')
tf.app.flags.DEFINE_integer('validation_shards', 128,
                            'Number of shards in validation TFRecord files.')

tf.app.flags.DEFINE_integer('num_threads', 8,
                            'Number of threads to preprocess the images.')

# The classes file contains a map of IDs and valid labels.
# Assumes that the file contains entries as such:
#   1 001.Black_footed_Albatross
#   2 002.Laysan_Albatross
#   3 003.Sooty_Albatross
# where each line corresponds to an ID and label.
tf.app.flags.DEFINE_string('classes_file', 'classes.txt', 'Classes file')

# The bounding boxes file contains the bounding box for each image
# Assumes that the file contains entries in the following format:
# <filename> <xmin> <ymin> <xmax> <ymax>
tf.app.flags.DEFINE_string('bounding_boxes_file', 'bounding_boxes.txt', 'Bounding boxes file')

# The data split file contains the train-test split for the images
# Assumes that the file contains entries as such:
# 1 0
# 2 1
# 3 0
# where each line has an image ID and a boolean for indicating if an example is in the train dataset
tf.app.flags.DEFINE_string('data_split_file', 'train_test_split.txt', 'Data split file')

# The images file contains the list of all images and corresponding number in the dataset
# Assumes that the file contains entries as such:
# 1 001.Black_footed_Albatross/Black_Footed_Albatross_0046_18.jpg
# ...
# 11788 200.Common_Yellowthroat/Common_Yellowthroat_0055_190967.jpg
# where each line has the number of the image in the dataset and the filename for that image
tf.app.flags.DEFINE_string('images_file', 'images.txt', 'Images file')


FLAGS = tf.app.flags.FLAGS


def _int64_feature(value):
  """Wrapper for inserting int64 features into Example proto."""
  if not isinstance(value, list):
    value = [value]
  return tf.train.Feature(int64_list=tf.train.Int64List(value=value))


def _float_feature(value):
  """Wrapper for inserting float features into Example proto."""
  if not isinstance(value, list):
    value = [value]
  return tf.train.Feature(float_list=tf.train.FloatList(value=value))


def _bytes_feature(value):
  """Wrapper for inserting bytes features into Example proto."""
  return tf.train.Feature(bytes_list=tf.train.BytesList(value=[value]))

def _int64_feature(value):
  return tf.train.Feature(int64_list=tf.train.Int64List(value=[value]))


def _int64_list_feature(value):
  return tf.train.Feature(int64_list=tf.train.Int64List(value=value))


def _bytes_feature(value):
  return tf.train.Feature(bytes_list=tf.train.BytesList(value=[value]))


def _bytes_list_feature(value):
  return tf.train.Feature(bytes_list=tf.train.BytesList(value=value))


def _float_list_feature(value):
  return tf.train.Feature(float_list=tf.train.FloatList(value=value))


def _convert_to_example(filename, image_buffer, label, text, bbox, height, width):
  """Build an Example proto for an example.
  Args:
    filename: string, path to an image file, e.g., '/path/to/example.JPG'
    image_buffer: string, JPEG encoding of RGB image
    label: integer, identifier for the ground truth for the network
    text: string, unique human-readable, e.g. 'dog'
    height: integer, image height in pixels
    width: integer, image width in pixels
  Returns:
    Example proto
  """

  xmin = []
  ymin = []
  xmax = []
  ymax = []
  for b in bbox:
    assert len(b) == 4
    [l.append(point) for l, point in zip([xmin, ymin, xmax, ymax], b)]

  colorspace = 'RGB'
  channels = 3
  image_format = 'JPEG'
  classes_text = []
  object = 'bird'
  classes_text.append(object.encode('utf8'))

  example = tf.train.Example(features=tf.train.Features(feature={
      'image/height': _int64_feature(height),
      'image/width': _int64_feature(width),
      'image/colorspace': _bytes_feature(tf.compat.as_bytes(colorspace)),
      'image/channels': _int64_feature(channels),
      'image/class/label': _int64_feature(label),
      'image/class/text': _bytes_feature(tf.compat.as_bytes(text)),
      'image/object/class/label': _int64_list_feature([1]),
      'image/object/class/text': _bytes_list_feature(classes_text),
      'image/object/bbox/xmin': _float_list_feature(xmin),
      'image/object/bbox/xmax': _float_list_feature(xmax),
      'image/object/bbox/ymin': _float_list_feature(ymin),
      'image/object/bbox/ymax': _float_list_feature(ymax),
      'image/format': _bytes_feature(tf.compat.as_bytes(image_format)),
      'image/filename': _bytes_feature(tf.compat.as_bytes(os.path.basename(filename))),
      'image/source_id': _bytes_feature(tf.compat.as_bytes(os.path.basename(filename))),
      'image/encoded': _bytes_feature(tf.compat.as_bytes(image_buffer))}))
  return example


class ImageCoder(object):
  """Helper class that provides TensorFlow image coding utilities."""

  def __init__(self):
    # Create a single Session to run all image coding calls.
    self._sess = tf.Session()

    # Initializes function that converts PNG to JPEG data.
    self._png_data = tf.placeholder(dtype=tf.string)
    image = tf.image.decode_png(self._png_data, channels=3)
    self._png_to_jpeg = tf.image.encode_jpeg(image, format='rgb', quality=100)

    # Initializes function that decodes RGB JPEG data.
    self._decode_jpeg_data = tf.placeholder(dtype=tf.string)
    self._decode_jpeg = tf.image.decode_jpeg(self._decode_jpeg_data, channels=3)

  def png_to_jpeg(self, image_data):
    return self._sess.run(self._png_to_jpeg,
                          feed_dict={self._png_data: image_data})

  def decode_jpeg(self, image_data):
    image = self._sess.run(self._decode_jpeg,
                           feed_dict={self._decode_jpeg_data: image_data})
    assert len(image.shape) == 3
    assert image.shape[2] == 3
    return image


def _is_png(filename):
  """Determine if a file contains a PNG format image.
  Args:
    filename: string, path of the image file.
  Returns:
    boolean indicating if the image is a PNG.
  """
  return filename.endswith('.png')


def _process_image(filename, coder):
  """Process a single image file.
  Args:
    filename: string, path to an image file e.g., '/path/to/example.JPG'.
    coder: instance of ImageCoder to provide TensorFlow image coding utils.
  Returns:
    image_buffer: string, JPEG encoding of RGB image.
    height: integer, image height in pixels.
    width: integer, image width in pixels.
  """
  # Read the image file.
  with tf.gfile.FastGFile(filename, 'rb') as f:
    image_data = f.read()

  # Convert any PNG to JPEG's for consistency.
  if _is_png(filename):
    print('Converting PNG to JPEG for %s' % filename)
    image_data = coder.png_to_jpeg(image_data)

  # Decode the RGB JPEG.
  image = coder.decode_jpeg(image_data)

  # Check that image converted to RGB
  assert len(image.shape) == 3
  height = image.shape[0]
  width = image.shape[1]
  assert image.shape[2] == 3

  return image_data, height, width


def _process_image_files_batch(coder, thread_index, ranges, name, filenames,
                               texts, labels, bboxes, num_shards):
  """Processes and saves list of images as TFRecord in 1 thread.
  Args:
    coder: instance of ImageCoder to provide TensorFlow image coding utils.
    thread_index: integer, unique batch to run index is within [0, len(ranges)).
    ranges: list of pairs of integers specifying ranges of each batches to
      analyze in parallel.
    name: string, unique identifier specifying the data set
    filenames: list of strings; each string is a path to an image file
    texts: list of strings; each string is human readable, e.g. 'dog'
    labels: list of integer; each integer identifies the ground truth
    num_shards: integer number of shards for this data set.
  """
  # Each thread produces N shards where N = int(num_shards / num_threads).
  # For instance, if num_shards = 128, and the num_threads = 2, then the first
  # thread would produce shards [0, 64).
  num_threads = len(ranges)
  assert not num_shards % num_threads
  num_shards_per_batch = int(num_shards / num_threads)

  shard_ranges = np.linspace(ranges[thread_index][0],
                             ranges[thread_index][1],
                             num_shards_per_batch + 1).astype(int)
  num_files_in_thread = ranges[thread_index][1] - ranges[thread_index][0]

  counter = 0
  for s in range(num_shards_per_batch):
    # Generate a sharded version of the file name, e.g. 'train-00002-of-00010'
    shard = thread_index * num_shards_per_batch + s
    output_filename = '%s-%.5d-of-%.5d' % (name, shard, num_shards)
    output_file = os.path.join(FLAGS.output_directory, name, output_filename)
    writer = tf.python_io.TFRecordWriter(output_file)

    shard_counter = 0
    files_in_shard = np.arange(shard_ranges[s], shard_ranges[s + 1], dtype=int)
    for i in files_in_shard:
      filename = filenames[i]
      label = labels[i]
      text = texts[i]
      bbox = bboxes[i]

      try:
        image_buffer, height, width = _process_image(filename, coder)
      except Exception as e:
        print(e)
        print('SKIPPED: Unexpected error while decoding %s.' % filename)
        continue

      example = _convert_to_example(filename, image_buffer, label,
                                    text, bbox, height, width)
      writer.write(example.SerializeToString())
      shard_counter += 1
      counter += 1

      if not counter % 1000:
        print('%s [thread %d]: Processed %d of %d images in thread batch.' %
              (datetime.now(), thread_index, counter, num_files_in_thread))
        sys.stdout.flush()

    writer.close()
    print('%s [thread %d]: Wrote %d images to %s' %
          (datetime.now(), thread_index, shard_counter, output_file))
    sys.stdout.flush()
    shard_counter = 0
  print('%s [thread %d]: Wrote %d images to %d shards.' %
        (datetime.now(), thread_index, counter, num_files_in_thread))
  sys.stdout.flush()


def _process_image_files(name, filenames, texts, labels, bboxes, num_shards):
  """Process and save list of images as TFRecord of Example protos.
  Args:
    name: string, unique identifier specifying the data set
    filenames: list of strings; each string is a path to an image file
    texts: list of strings; each string is human readable, e.g. 'dog'
    labels: list of integer; each integer identifies the ground truth
    bboxes: list of bounding boxes for each image
    num_shards: integer number of shards for this data set.
  """
  assert len(filenames) == len(texts)
  assert len(filenames) == len(labels)
  assert len(filenames) == len(bboxes)

  # Break all images into batches with a [ranges[i][0], ranges[i][1]].
  spacing = np.linspace(0, len(filenames), FLAGS.num_threads + 1).astype(np.int)
  ranges = []
  for i in range(len(spacing) - 1):
    ranges.append([spacing[i], spacing[i + 1]])

  # Launch a thread for each batch.
  print('Launching %d threads for spacings: %s' % (FLAGS.num_threads, ranges))
  sys.stdout.flush()

  # Create a mechanism for monitoring when all threads are finished.
  coord = tf.train.Coordinator()

  # Create a generic TensorFlow-based utility for converting all image codings.
  coder = ImageCoder()

  threads = []
  for thread_index in range(len(ranges)):
    args = (coder, thread_index, ranges, name, filenames,
            texts, labels, bboxes, num_shards)
    t = threading.Thread(target=_process_image_files_batch, args=args)
    t.start()
    threads.append(t)

  # Wait for all the threads to terminate.
  coord.join(threads)
  print('%s: Finished writing all %d images in data set.' %
        (datetime.now(), len(filenames)))
  sys.stdout.flush()


def _find_image_files(data_dir, classes_file):
  """Build a list of all images files and labels in the data set.
  Args:
    data_dir: string, path to the root directory of images.
      Assumes that the image data set resides in JPEG files located in
      the following directory structure.
        data_dir/dog/another-image.JPEG
        data_dir/dog/my-image.jpg
      where 'dog' is the label associated with these images.
    classes_file: string, path to the images file.
      The list of classes are held in this file. Assumes that the file
      contains entries as such:
        1 001.Black_footed_Albatross
        2 002.Laysan_Albatross
        3 003.Sooty_Albatross
      where each line corresponds to an image.
  Returns:
    filenames: list of strings; each string is a path to an image file.
    texts: list of strings; each string is the class, e.g. 'dog'
    labels: list of integer; each integer identifies the ground truth.
  """
  print('Determining list of input files and labels from %s.' % data_dir)
  unique_labels = [l.split()[1] for l in tf.gfile.FastGFile(
      classes_file, 'r').readlines()]

  labels = []
  filenames = []
  texts = []

  # Leave label index 0 empty as a background class.
  label_index = 1

  # Construct the list of JPEG files and labels.
  for text in unique_labels:
    jpeg_file_path = '%s/%s/*' % (data_dir, text)
    matching_files = tf.gfile.Glob(jpeg_file_path)

    labels.extend([label_index] * len(matching_files))
    texts.extend([text] * len(matching_files))
    filenames.extend(matching_files)

    if not label_index % 100:
      print('Finished finding files in %d of %d classes.' % (
          label_index, len(labels)))
    label_index += 1

  # Shuffle the ordering of all image files in order to guarantee
  # random ordering of the images with respect to label in the
  # saved TFRecord files. Make the randomization repeatable.
  shuffled_index = list(range(len(filenames)))
  random.seed(12345)
  random.shuffle(shuffled_index)

  filenames = [filenames[i] for i in shuffled_index]
  texts = [texts[i] for i in shuffled_index]
  labels = [labels[i] for i in shuffled_index]

  print('Found %d JPEG files across %d labels inside %s.' %
        (len(filenames), len(unique_labels), data_dir))
  return filenames, texts, labels


def _find_image_bounding_boxes(filenames, image_to_bboxes):
  """Find the bounding boxes for a given image file.
    Args:
      filenames: list of strings; each string is a path to an image file.
      image_to_bboxes: dictionary mapping image file names to a list of
        bounding boxes. This list contains 0+ bounding boxes.
    Returns:
      List of bounding boxes for each image. Note that each entry in this
      list might contain from 0+ entries corresponding to the number of bounding
      box annotations for the image.
    """
  num_image_bbox = 0
  bboxes = []
  for f in filenames:
    basename = os.path.basename(f)
    if basename in image_to_bboxes:
      bboxes.append(image_to_bboxes[basename])
      num_image_bbox += 1
    else:
      bboxes.append([])
  print('Found %d images with bboxes out of %d images' % (
    num_image_bbox, len(filenames)))
  return bboxes


def _build_bounding_box_lookup(bounding_boxes_file):
  """Build dictionary to retrieve bounding box for image filename
  Args:
    bounding_boxes_file: file containing processed bounding boxes.
      Entries are in the form: <filename> <xmin> <ymin> <xmax> <ymax>
  """
  lines = tf.gfile.FastGFile(bounding_boxes_file, 'r').readlines()
  images_to_bboxes = {}
  num_bbox = 0
  num_image = 0
  for l in lines:
    if l:
      parts = l.split()
      assert len(parts) == 5, ('Failed to parse: %s' % l)
      filename = parts[0]
      xmin = float(parts[1])
      ymin = float(parts[2])
      xmax = float(parts[3])
      ymax = float(parts[4])
      box = [xmin, ymin, xmax, ymax]

      if filename not in images_to_bboxes:
        images_to_bboxes[filename] = []
        num_image += 1
      images_to_bboxes[filename].append(box)
      num_bbox += 1

  print('Successfully read %d bounding boxes '
        'across %d images.' % (num_bbox, num_image))
  return images_to_bboxes


def _build_dataset_split_lookup(data_split_file, images_file):
  """Build dictionary to retrieve data assignment (train, test, validation) for image
  Args:
    data_split_file: file containing set assignment for each image
    images_file: file containing image numbers and file names
  """
  split_lines = tf.gfile.FastGFile(data_split_file, 'r').readlines()
  images_lines = tf.gfile.FastGFile(images_file, 'r').readlines()
  images_to_dataset = {}

  num_assignments = 0
  num_images = 0
  dataset = ''

  for (sl, il) in zip(split_lines, images_lines):
    if sl and il:
      split_parts = sl.split()
      image_parts = il.split()
      assert len(split_parts) == 2, ('Failed to parse: %s' % sl)
      assert len(image_parts) == 2, ('Failed to parse: %s' % il)
      num = split_parts[0]
      assert num == image_parts[0], ('Incongruence between %s and %s' % (sl, il))

      # Determine proper dataset (training images are assigned to the validation directory with probability 1/10)
      validation_set_size = 400
      current_validation_set_size = 0
      if split_parts[1] == '0':
        dataset = 'test'
      else:
        r = random.randint(1, 101)
        if r <= 10 and current_validation_set_size < validation_set_size:
          current_validation_set_size += 1
          dataset = 'validation'
        else:
          dataset = 'train'
      filename = image_parts[1].split('/')[1]

      if filename not in images_to_dataset:
        images_to_dataset[filename] = dataset
        num_images += 1
      num_assignments += 1

  print('Successfully read %d dataset assignments '
        'across %d images.' % (num_assignments, num_images))
  return images_to_dataset


def _process_dataset(name, directory, num_shards, classes_file, images_to_bboxes, images_to_dataset):
  """Process a complete data set and save it as a TFRecord.
  Args:
    name: string, unique identifier specifying the data set.
    directory: string, root path to the data set.
    num_shards: integer number of shards for this data set.
    classes_file: string, path to the classes file.
    images_to_bboxes: dictionary mapping image file names to bounding boxes
  """
  # Finds all filenames, texts, and labels
  filenames, texts, labels = _find_image_files(directory, classes_file)

  # Lists to contain filenames, texts, and labels relevant to dataset specified by name parameter
  filtered_filenames = []
  filtered_texts = []
  filtered_labels = []
  bboxes = []

  for (fn, t, l) in zip(filenames, texts, labels):
    filename = fn.split('/')
    filename_suffix = filename[len(filename) - 1]
    if images_to_dataset[filename_suffix] == name:
        filtered_filenames.append(fn)
        filtered_texts.append(t)
        filtered_labels.append(l)
    bboxes = _find_image_bounding_boxes(filtered_filenames, images_to_bboxes)

  if not os.path.exists(os.path.join(FLAGS.output_directory, name)):
      os.mkdirs(os.path.join(FLAGS.output_directory, name))
  _process_image_files(name, filtered_filenames, filtered_texts, filtered_labels, bboxes, num_shards)


def main(unused_argv):
  assert not FLAGS.train_shards % FLAGS.num_threads, (
      'Please make the FLAGS.num_threads commensurate with FLAGS.train_shards')
  assert not FLAGS.validation_shards % FLAGS.num_threads, (
      'Please make the FLAGS.num_threads commensurate with '
      'FLAGS.validation_shards')
  print('Saving results to %s' % FLAGS.output_directory)

  # Build map from filename to bounding box
  images_to_bboxes = _build_bounding_box_lookup(FLAGS.bounding_boxes_file)

  # Build map from filename to data set (train, validation)
  images_to_dataset = _build_dataset_split_lookup(FLAGS.data_split_file, FLAGS.images_file)

  # Run it!
  _process_dataset('validation', FLAGS.images_directory,
                   FLAGS.validation_shards, FLAGS.classes_file, images_to_bboxes, images_to_dataset)
  _process_dataset('train', FLAGS.images_directory,
                   FLAGS.train_shards, FLAGS.classes_file, images_to_bboxes, images_to_dataset)
  _process_dataset('test', FLAGS.images_directory,
                   1, FLAGS.classes_file, images_to_bboxes, images_to_dataset)

if __name__ == '__main__':
  tf.app.run()
