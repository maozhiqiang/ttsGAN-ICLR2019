import argparse
import os
from multiprocessing import cpu_count
from tqdm import tqdm
from datasets import emotiondata
from hparams import hparams


def preprocess_emotion(args):
  in_dir = args.in_dir
  out_dir = os.path.join(args.base_dir, args.output)
  os.makedirs(out_dir, exist_ok=True)
  metadata = emotiondata.build_from_path(in_dir, out_dir, args.num_workers, tqdm=tqdm)
  write_metadata(metadata, out_dir)

def write_metadata(metadata, out_dir):
  with open(os.path.join(out_dir, 'metadata.txt'), 'w', encoding='utf-16') as f:
    for m in metadata:
      f.write('|'.join([str(x) for x in m]) + '\n')
  frames = sum([m[3] for m in metadata])
  hours = frames * hparams.frame_shift_ms / (3600 * 1000)
  print('wrote %d utterances, %d frames (%.2f hours)' %(len(metadata), frames, hours))
  # print('Max input length: %d' % max(m[3] for m in metadata))
  print('Max output length: %d' % max(m[3] for m in metadata))

def main():
  parser = argparse.ArgumentParser()
  parser.add_argument('--in_dir', default='//vibe15/PublicAll/STCM-101/Zo')
  parser.add_argument('--base_dir', default=os.getcwd())
  parser.add_argument('--output', default='data_emt4')
  parser.add_argument('--dataset', required=True, choices=['emotion', 'identity'])
  parser.add_argument('--num_workers', type=int, default=cpu_count())
  args = parser.parse_args()
  preprocess_emotion(args)

if __name__ == '__main__':
  main()

