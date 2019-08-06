# -*- coding: utf-8 -*-
import os
import argparse
import names
from random import shuffle, choice
from my_utils import parse_seg_file, re_sort_metadata
from generate import load_model, get_seed_sent, masked_decoding
from pytorch_pretrained_bert import BertTokenizer, BertForMaskedLM, BertForMaskedLM
from pyclustering.cluster.kmedoids import kmedoids
#from sklearn.cluster import SpectralClustering, AgglomerativeClustering
import numpy as np
#import textdistance # .lcsstr for consecutive; otherwise; .lcsseq
    

bert_version = "bert-large-uncased" # bert-(base|large)-(un)?cased
#print(f"Loading BERT ({bert_version})...")
#tokenizer = BertTokenizer.from_pretrained(bert_version)
#model = load_model(bert_version)
#print("BERT loaded.")

EOS = "<eos>"
NUMBER = "<num>"    
UNK = "<unk>"
MASK = "[MASK]"
seqs=None #debug only

def compute_lcs(X, Y): 
    UP_LEFT,UP,LEFT = 0,1,2
    m,n = len(X),len(Y)
    L = [[None]*(n + 1) for i in range(m + 1)] # L[i][j] contains length of LCS of X[0..i-1] and Y[0..j-1]
    V = [[None]*(n + 1) for i in range(m + 1)] # for recovering lcs
    
    def recover_lcs():
        lcs = []
        i,j = m,n
        while i > 0 and j > 0:
            if V[i][j] == UP_LEFT:
                lcs = [X[i-1]] + lcs
                i,j = i-1,j-1
            else:
                (i,j) = (i-1,j) if V[i][j] == UP else (i,j-1)
        return lcs

    for i in range(m + 1): 
        for j in range(n + 1): 
            if i == 0 or j == 0 : 
                L[i][j] = 0
            elif X[i-1] == Y[j-1]: 
                L[i][j] = L[i-1][j-1]+1
                V[i][j] = UP_LEFT
            else: 
                L[i][j] = max(L[i-1][j], L[i][j-1]) 
                V[i][j] = UP if L[i][j]==L[i-1][j] else LEFT
    
    return L[m][n], recover_lcs()

def get_lcs_sim_mat(seqs):
    """
    Paramters:
    ---------
        seqs    list of list
            e.g. [[0,1,2],[3,1]] is for 2 MWPs and variable-length input

    Returns:
    ---------
        len(seqs) x len(seqs) numpy similarity matrix
    """
    print("Computing lcs similarity matrix...")
    sim = np.zeros((len(seqs),len(seqs)))
    lcss = [[None]*len(seqs) for _ in range(len(seqs))]
    for si1,s1 in enumerate(seqs):
        for si2,s2 in enumerate(seqs):
            if si2 < si1: # Lower triangular, id2 < id1
                lcs_len,lcs = compute_lcs(s1, s2)
                sim[si1,si2] = lcs_len +1 # connected graph
                lcss[si1][si2] = lcs
    sim = sim + sim.transpose() # Full
    #scheme == 'editdist': # hamming distance is only for comparing strings of the same length
    return sim,lcss
    

def get_template_seqs(seg_path):
    linenos, temps2sents, top_temps = parse_seg_file(seg_path)
    return top_temps[:30], linenos, temps2sents # HACK for debugging

def get_mwp_seqs(data_path):
    seqs= None,None
    with open(os.path.join(data_path,'train.txt'),'r',encoding='utf-8') as f:
        seqs = [line.split('|||')[0].split() for line in list(f.readlines())][:10] # HACK for debugging
    return seqs

def cluster(seqs):#, n_clusters):#, scheme):
    ## Commented code doesn't make sense. '
    ## ab' and 'ac' is close; 'ab' and 'db' is close; 
    ## but 'ac' and 'db' should not be clustered together. 
    ## LCS does not imply mutual similarity of multiple strings.
    #clusters = None
    #if scheme == 'spectral':
    #    clustering = SpectralClustering(n_clusters=n_clusters,affinity='precomputed').fit(matrix)
    #elif scheme == 'hierarchical':
    #clustering = AgglomerativeClustering(n_clusters=n_clusters,affinity='precomputed',linkage='average').fit(matrix)
    ## clustering.labels_ is a list of labels indicating the cluster each seq belongs to
    #clusters = [[] for _ in range(n_clusters)]
    #for lid,l in enumerate(clustering.labels_):
    #   clusters[l].append(lid) 
    print("cluster:",seqs)
    matrix,lcss = get_lcs_sim_mat(seqs)
    indices = sorted([matrix.argmax(None)//len(seqs), matrix.argmax(None)%len(seqs)],reverse=True)
    new_seqs = [s for si,s in enumerate(seqs) if si not in indices]
    new_seqs.append(lcss[indices[0]][indices[1]])   # The newest cluster is put last
    return new_seqs # [[0],[1,5],[2],[3,4]] is 4 clusters, each of several indices corresponding to seqs


def read_ner_file(nerf_path):
    """
    Read the ners & nums from training data to recover them
    # Not necessary. Do it later.
    """
    ners = []
    with open(nerf_path,'r') as f:
        pass

def substitute_seg(seg_path, data_path):
    n_preds = 5
    n_items = 30 # How many <PER_i>, <num>, ...?
    # TODO: Get nums & names (only) from training data
    nums = [str(i) for i in range(n_items)]  # Different preds shares same numbers
    shuffle(nums)
    nams = [[names.get_first_name() for i in range(n_items)] for j in range(n_preds)]
    
    fout = open("berttest.in",'w',encoding='utf-8')
    linenos, temps2sents, top_temps = parse_seg_file(seg_path)
    metadata = re_sort_metadata(os.path.join(data_path,'metadata_train.tsv'), linenos, new_idxname='seg_linenos')

    train_tagged = list((open(os.path.join(data_path,"train.txt"),'r',encoding='utf-8')).readlines())
    tags = [[tuple(int(a) for a in t.split(',')) for t in line.strip().split('|||')[1].split()] for line in train_tagged]

    for temp in top_temps:
        for (segments,lineno) in temps2sents[temp]:
            
            #print("IN:"," ".join(segments),lineno)
            fixed_rand_wid = choice(tags[linenos[lineno]][:-1])[0]
            for pid in range(n_preds):
                # Handle: <PER_i>, <unk>, <num>
                numid,n_masks = 0,0
                new_tokens = []
                for segment in segments:
                    tokens = segment.split()
                    for token in tokens:
                        if token == NUMBER:
                            token = nums[numid] # Assume each number is different
                            numid += 1
                        elif token.startswith('<PER_'):
                            namid = int(token[5:-1])-1   # 1-indexed to 0-indexed
                            token = nams[pid][namid]
                        elif token == UNK:
                            token = MASK
                        elif token == EOS:
                            break
                        new_tokens.append(token)
                # TODO so what do i want to mask??? nouns?
                # Read the src_ and randomly mask some word for now
                if n_masks == 0:
                    try:
                        new_tokens[ fixed_rand_wid ] = MASK
                    except Exception as e:
                        print(e)
                        continue
                new_sent = " ".join(new_tokens)
                #print("OUT:",new_sent)
                fout.writelines(new_sent+"\n")
    fout.close()
    

def test_bert():#seed_sentence, masking):
    #seed_sentence = "Two [MASK] weighs 3 [MASK] . How much do 3 [MASK] weigh ?"
    #seed_sentence = "There are <num> candies in <PER_1> 's candy collection . If the candies are organized into <num> groups , how big is each group ?"
    #seed_sentence = "There are 3 [MASK] in Mary 's [MASK] collection . If the [MASK] are organized into 3 groups , how big is each group ?"
    #seed_sentence = "<PER_1> has <num>|271 books|40 . <num> are <unk>|215 school|180 and the|148 rest|42 are <unk>|182 <unk>|53 . How many|162 books|251 <unk>|72 <unk>|222 does <PER_1> have|214 ? <eos>|139 "
    #masking = "none" #','.join([str(sid) for sid,s in enumerate(seed_sentence.split()) if s.startswith('<')])
    lines_in = list((open("berttest.in", 'r',encoding='utf-8')).readlines())
    fout = open("berttest.out",'w',encoding='utf-8')
    for seed_sentence in lines_in:
        toks, seg_tensor, mask_ids = get_seed_sent(seed_sentence, tokenizer, masking="none", n_append_mask=0)
        toks = masked_decoding(toks, seg_tensor, mask_ids, model, tokenizer, "argmax")
        print("Final: %s" % (" ".join(toks)))
        fout.writelines(" ".join(toks)+'\n')
    fout.close()

parser = argparse.ArgumentParser(description='')
parser.add_argument('-seg_path', type=str, default='', help='path to seg file')
parser.add_argument('-data_path', type=str, default='', help='path to data dir')

if __name__ == "__main__":
    args = parser.parse_args()
    print(args)

    seg_path = args.seg_path
    data_path = args.data_path
    #substitute_seg(seg_path, args.data_path)
    #test_bert()#seed_sentence, masking) 

    top_temps, linenos, temps2sents = get_template_seqs(seg_path)
    seqs = top_temps
    #seqs = get_mwp_seqs(data_path)
    n_clusters = 3
    while len(seqs) > n_clusters:
        seqs = cluster(seqs)
    print(seqs)