import argparse
import numpy as np
import os
import re
import random
import tensorflow as tf
import threading
import time
import traceback
from text import cmudict, text_to_sequence
from util.infolog import log
from hparams import hparams

_batches_per_group = 32
_p_cmudict = 0.5
_pad = 0

class DataFeeder(threading.Thread):
  '''Feeds batches of data into a queue on a background thread.'''

  def __init__(self, coordinator, metadata_filename_pos, metadata_filename_neg, hparams):
    super(DataFeeder, self).__init__()
    self._coord = coordinator
    self._hparams = hparams
    self._cleaner_names = [x.strip() for x in hparams.cleaners.split(',')]
    self._offset = 0

    # Load metadata:
    # load data from both positive filename and negative filename
    self._datadir = os.path.dirname(metadata_filename_pos)
    #self._datadir_neg = os.path.dirname(metadata_filename_neg)

    with open(metadata_filename_pos, encoding='utf-16') as f:
      self._metadata_pos = [line.strip().split('|') for line in f]
      hours = sum((int(x[2]) for x in self._metadata_pos)) * hparams.frame_shift_ms / (3600 * 1000)
      log('Loaded positive metadata for %d examples (%.2f hours)' % (len(self._metadata_pos), hours))
    
    with open(metadata_filename_neg, encoding='utf-16') as f:
      self._metadata_neg = [line.strip().split('|') for line in f]
      hours = sum((int(x[2]) for x in self._metadata_neg)) * hparams.frame_shift_ms / (3600 * 1000)
      log('Loaded negative metadata for %d examples (%.2f hours)' % (len(self._metadata_neg), hours))
 


    # Create placeholders for inputs and targets. Don't specify batch size because we want to
    # be able to feed different sized batches at eval time.
    self._placeholders = [
      tf.placeholder(tf.int32, [None, None], 'inputs_pos'),
      tf.placeholder(tf.int32, [None], 'input_lengths_pos'),
      tf.placeholder(tf.float32, [None, None, hparams.num_mels], 'mel_targets_pos'),
      tf.placeholder(tf.float32, [None, None, hparams.num_freq], 'linear_targets_pos'),     
      tf.placeholder(tf.int32, [None, None], 'inputs_neg'),
      tf.placeholder(tf.int32, [None], 'input_lengths_neg'),
      tf.placeholder(tf.float32, [None, None, hparams.num_mels], 'mel_targets_neg'),
      tf.placeholder(tf.float32, [None, None, hparams.num_freq], 'linear_targets_neg'),
      tf.placeholder(tf.int32, [None, 4], 'pos_labels'),
      tf.placeholder(tf.int32, [None, 4], 'neg_labels')
    ]
     
    # Create queue for buffering data:
    queue = tf.FIFOQueue(16, [tf.int32, tf.int32, tf.float32, tf.float32, tf.int32, tf.int32, tf.float32,
                              tf.float32, tf.int32, tf.int32], name='input_queue')
    self._enqueue_op = queue.enqueue(self._placeholders)
    self.inputs_pos, self.input_lengths_pos, self.mel_targets_pos, self.linear_targets_pos,self.inputs_neg, \
        self.input_lengths_neg, self.mel_targets_neg, self.linear_targets_neg, self.labels_pos, self.labels_neg = queue.dequeue()
    
    self.inputs_pos.set_shape(self._placeholders[0].shape)
    self.input_lengths_pos.set_shape(self._placeholders[1].shape)
    self.mel_targets_pos.set_shape(self._placeholders[2].shape)
    self.linear_targets_pos.set_shape(self._placeholders[3].shape)
    
    self.inputs_neg.set_shape(self._placeholders[0].shape)
    self.input_lengths_neg.set_shape(self._placeholders[1].shape)
    self.mel_targets_neg.set_shape(self._placeholders[2].shape)
    self.linear_targets_neg.set_shape(self._placeholders[3].shape)

    self.labels_pos.set_shape(self._placeholders[8].shape)
    self.labels_neg.set_shape(self._placeholders[9].shape)


    # Load CMUDict: If enabled, this will randomly substitute some words in the training data with
    # their ARPABet equivalents, which will allow you to also pass ARPABet to the model for
    # synthesis (useful for proper nouns, etc.)
    if hparams.use_cmudict:
      cmudict_path = os.path.join(self._datadir, 'cmudict-0.7b')
      if not os.path.isfile(cmudict_path):
        raise Exception('If use_cmudict=True, you must download cmu dictionary first. ' +
          'Run shell as:\n wget -P %s http://svn.code.sf.net/p/cmusphinx/code/trunk/cmudict/cmudict-0.7b'  % self._datadir)
      self._cmudict = cmudict.CMUDict(cmudict_path, keep_ambiguous=False)
      log('Loaded CMUDict with %d unambiguous entries' % len(self._cmudict))
    else:
      self._cmudict = None


  def start_in_session(self, session):
    self._session = session
    self.start()


  def run(self):
    try:
      while not self._coord.should_stop():
        self._enqueue_next_group()
    except Exception as e:
      traceback.print_exc()
      self._coord.request_stop(e)


  def _enqueue_next_group(self):
    start = time.time()

    # Read a group of examples:
    n = self._hparams.batch_size
    r = self._hparams.outputs_per_step
    examples = [self._get_next_example() for i in range(n * _batches_per_group)]

    # Bucket examples based on similar output sequ ence length for efficiency:
    examples.sort(key=lambda x: x[-3])
    batches = [examples[i:i+n] for i in range(0, len(examples), n)]
    random.shuffle(batches)

    log('Generated %d batches of size %d in %.03f sec' % (len(batches), n, time.time() - start))
    for batch in batches:
      feed_dict = dict(zip(self._placeholders, _prepare_batch(batch, r)))
      self._session.run(self._enqueue_op, feed_dict=feed_dict)


  def _get_next_example(self):
    '''Loads a single example (input, mel_target, linear_target, cost) from disk'''
    if self._offset >= len(self._metadata_pos):
      self._offset = 0
      random.shuffle(self._metadata_pos)
      random.shuffle(self._metadata_neg)
    meta_pos = self._metadata_pos[self._offset]
    meta_neg = self._metadata_neg[self._offset]
    self._offset += 1

    #adds a space between punctuation and the word
    _punctuation_re = re.compile(r'([\.,"\-_:]+)')
    text_pos = re.sub(_punctuation_re, r' \1 ', meta_pos[3])
    text_neg = re.sub(_punctuation_re, r' \1 ', meta_neg[3])

    if self._cmudict and random.random() < _p_cmudict:
      text_pos = ' '.join([self._maybe_get_arpabet(word) for word in text_pos.split(' ')])
      text_neg = ' '.join([self._maybe_get_arpabet(word) for word in text_neg.split(' ')])

    input_data_pos = np.asarray(text_to_sequence(text_pos, self._cleaner_names), dtype=np.int32)
    input_data_neg = np.asarray(text_to_sequence(text_neg, self._cleaner_names), dtype=np.int32)

    linear_target_pos = np.load(os.path.join(self._datadir, meta_pos[0]))
    mel_target_pos = np.load(os.path.join(self._datadir, meta_pos[1]))
    linear_target_neg = np.load(os.path.join(self._datadir, meta_neg[0]))
    mel_target_neg = np.load(os.path.join(self._datadir, meta_neg[1]))

    idx_pos = int(meta_pos[-1])
    idx_neg = int(meta_neg[-1])
    label_pos = np.zeros(4)
    label_neg = np.zeros(4)
    label_pos[idx_pos] = 1.
    label_neg[idx_neg] = 1.

    return (input_data_pos, mel_target_pos, linear_target_pos, len(linear_target_pos), input_data_neg, mel_target_neg,
            linear_target_neg, len(linear_target_neg), label_pos, label_neg)


  def _maybe_get_arpabet(self, word):
    arpabet = self._cmudict.lookup(word)
    return '{%s}' % arpabet[0] if arpabet is not None and random.random() < 0.5 else word


def _prepare_batch(batch, outputs_per_step):
  random.shuffle(batch)
  inputs_pos = _prepare_inputs([x[0] for x in batch])
  input_lengths_pos = np.asarray([len(x[0]) for x in batch], dtype=np.int32)
  mel_targets_pos = _prepare_targets([x[1] for x in batch], outputs_per_step)
  linear_targets_pos = _prepare_targets([x[2] for x in batch], outputs_per_step)
  
  inputs_neg = _prepare_inputs([x[4] for x in batch])
  input_lengths_neg = np.asarray([len(x[4]) for x in batch], dtype=np.int32)
  mel_targets_neg = _prepare_targets([x[5] for x in batch], outputs_per_step)
  linear_targets_neg = _prepare_targets([x[6] for x in batch], outputs_per_step)

  labels_pos = np.stack(x[-2] for x in batch)
  labels_neg = np.stack(x[-1] for x in batch)

  return (inputs_pos, input_lengths_pos, mel_targets_pos, linear_targets_pos, inputs_neg, input_lengths_neg, mel_targets_neg, linear_targets_neg, labels_pos, labels_neg)


def _prepare_inputs(inputs):
  max_len = max((len(x) for x in inputs))
  return np.stack([_pad_input(x, max_len) for x in inputs])


def _prepare_targets(targets, alignment):
  max_len = max((len(t) for t in targets)) + 1
  return np.stack([_pad_target(t, _round_up(max_len, alignment)) for t in targets])


def _pad_input(x, length):
  return np.pad(x, (0, length - x.shape[0]), mode='constant', constant_values=_pad)


def _pad_target(t, length):
  return np.pad(t, [(0, length - t.shape[0]), (0,0)], mode='constant', constant_values=_pad)


def _round_up(x, multiple):
  remainder = x % multiple
  return x if remainder == 0 else x + multiple - remainder

def test():
  parser = argparse.ArgumentParser()
  parser.add_argument('--base_dir', default=os.path.dirname(os.getcwd()))
  parser.add_argument('--input_pos', default='training/train-pos.txt')
  parser.add_argument('--input_neg', default='training/train-neg.txt')
  parser.add_argument('--hparams', default='',
    help='Hyperparameter overrides as a comma-separated list of name=value pairs')

  args = parser.parse_args()
  hparams.parse(args.hparams)

  input_path_pos = os.path.join(args.base_dir, args.input_pos)
  input_path_neg = os.path.join(args.base_dir, args.input_neg)

  coord = tf.train.Coordinator()
  feeder = DataFeeder(coord, input_path_pos, input_path_neg, hparams)

if __name__ == '__main__':
  test()