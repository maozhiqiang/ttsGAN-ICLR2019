import os
import pandas as pd
import pickle

folder_emt_data = '//vibe15/PublicAll/STCM-101/Zo'
folder_data = os.path.join(os.getcwd(),'data_emt4')

def create_all_txt_pickle():
  folder_in = os.path.join(folder_emt_data,'Script')
  all_txt_pickle = os.path.join(folder_data,'emt4_all_txt.pickle')
  os.makedirs(folder_data, exist_ok=True)

  df = pd.DataFrame([], columns=['filename', 'script'])
  for i, fname in enumerate(os.listdir(folder_in)):
    with open(os.path.join(folder_in, fname), 'r') as infile:
      for j, line in enumerate(infile.readlines()):
        line = line.lstrip('ÿþ').rstrip('\n').split('\t') #'ÿþ' is beginning of file invalid character
        if len(line) < 2: #too short means it's just a blank line
          continue
        df = df.append(pd.DataFrame([line], columns=['filename', 'script']), ignore_index=True)

  with open(all_txt_pickle, 'wb') as file:
    pickle.dump(df, file)

def open_all_txt_pickle():
  all_txt_pickle = os.path.join(folder_data, 'emt4_all_txt.pickle')
  with open(all_txt_pickle, 'rb') as file:
    df = pickle.load(file)
  return(df)

def read_all_txt():
  all_txt_path = os.path.join(folder_data, 'all_txt_wav.txt')
  df = pd.read_csv(all_txt_path, sep='|', index_col=0,
                   names=['filename','script','emotion_label'])
  df.emotion_label = df.emotion_label.apply(int)
  return(df)

if __name__ == '__main__':
  # create_all_txt_pickle()
  read_all_txt()