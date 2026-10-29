"""Fit + gate for the RECALL banner scorer (#668): trains the pointwise logistic on LoCoMo
conversations 2-9, scores conversations 0-1 (hit@8/hit@20, baseline = recall order).
Run: LOCOMO_TRAIN=<dir> LOCOMO_TEST=<dir> python benchmark/recall_banner_fit.py (dirs hold conv_N/{qa,diamap}.json + deep.jsonl)."""
import os, sys, json, re, time
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from membrane import recall_banner as RB
TRAIN=os.environ.get('LOCOMO_TRAIN','locomo/results/doc')+'/conv_%d'
TEST=os.environ.get('LOCOMO_TEST','locomo/results/doc')+'/conv_%d'
N=50
def load(d):
    qa=json.load(open(d+'/qa.json')); dm=json.load(open(d+'/diamap.json')); out=[]
    for l in open(d+'/deep.jsonl'):
        a=json.loads(l); q=qa[a['qi']]
        if not 1<=q.get('category',0)<=4: continue
        ev={dm[e]['n'] for e in q.get('evidence',[]) if e in dm}
        if ev: out.append((q['question'],ev,a['k50'][:N]))
    return out
def rows_of(hs):
    out=[]
    for h in hs:
        m=re.search(r"\|\|\s*(.+?)\s*\((.*?)\):\s*(.*)", h['text'], re.S)
        spk,body=(m.group(1),m.group(3)) if m else ('',h['text'])
        out.append({'sim':h['sim'],'answer':[body],'a_n':sorted(h['events']),'q_n':None,'speaker':spk})
    return out
KEYS=['sim','inv_rank','bm25','overlap','speaker','neighbour']+[]
def X_of(q,hs):
    F=RB.features(rows_of(hs),q); return np.array([[f[k] for k in KEYS] for f in F])
def lab(ev,hs): return np.array([1.0 if set(h['events'])&ev else 0.0 for h in hs])
def fit(Z,t,l2=1e-3,iters=800,lr=0.1):
    mu,sd=Z.mean(0),Z.std(0)+1e-9; Zs=(Z-mu)/sd; w=np.zeros(Z.shape[1]); b=0.0
    for _ in range(iters):
        p=1/(1+np.exp(-(Zs@w+b))); w-=lr*(Zs.T@(p-t)/len(t)+l2*w); b-=lr*(p-t).mean()
    return w/sd, b-float((w*mu/sd).sum())
if __name__=='__main__':
    tr=[x for c in range(2,10) for x in load(TRAIN%c)]
    te=[x for c in (0,1) for x in load(TEST%c)]
    Xs=[X_of(q,hs) for q,ev,hs in tr]; ys=[lab(ev,hs) for q,ev,hs in tr]
    w,b=fit(np.vstack(Xs),np.concatenate(ys))
    W={'bias':float(b),**{k:float(v) for k,v in zip(KEYS,w)}}
    h8=b8=h20=b20=0; perq=[]
    for q,ev,hs in te:
        r=RB.rank(rows_of(hs),q,W); 
        hit=lambda ords,k: any(set(hs[i]['events'])&ev for i in ords[:k])
        idx={tuple(x['a_n']):i for i,x in enumerate(rows_of(hs))}
        order=[ [i for i,x in enumerate(hs) if sorted(x['events'])==rr['a_n']][0] for rr in r]
        perq.append({'q':q,'base8':bool(hit(range(N),8)),'scorer8':bool(hit(order,8))})
        h8+=hit(order,8); h20+=hit(order,20); b8+=hit(range(N),8); b20+=hit(range(N),20)
    print('train q',len(tr),'test q',len(te),'baseline',b8,b20,'scorer',h8,h20)
    print(json.dumps(W))

    json.dump(perq,open(os.environ.get('PERQ','benchmark/recall_banner_perq.json'),'w'))
