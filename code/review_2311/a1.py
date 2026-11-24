import csv,re,collections,datetime,io,sys
out=io.StringIO()
def P(*a):
    print(*a,file=out)
BNB=700.0
rows=list(csv.DictReader(open(r'D:\bot\analysis\hop_trades.csv',encoding='utf-8-sig'),delimiter=';'))
def f(x):
    try:return float(x)
    except:return 0.0
for r in rows:
    m=re.match(r'сеть (\d+)',r['reason'])
    r['net']=int(m.group(1)) if m else (1 if r['reason'].startswith('сигнал') else 0)
    r['day']=r['time'][:10]
    r['kind']='signal' if 'сигнал' in r['reason'] else 'chain'
    r['pnl']=f(r['pnl_bnb']);r['stake']=f(r['stake_bnb'])
P('ВСЕГО строк',len(rows),'период',rows[0]['time'],rows[-1]['time'])
def agg(keyf,flt=lambda r:True,title=''):
    d=collections.defaultdict(lambda:[0,0,0.0,0.0])
    for r in rows:
        if not flt(r):continue
        k=keyf(r);a=d[k];a[0]+=1;a[1]+=r['pnl']>0;a[2]+=r['pnl'];a[3]+=r['stake']
    P('##',title)
    for k in sorted(d):
        a=d[k];P(f'{k}: n={a[0]} плюс={a[1]} pnl={a[2]:+.5f} BNB (~${a[2]*BNB:+.1f}) ставка_сумма={a[3]:.4f}')
agg(lambda r:r['kind'],title='по типу')
agg(lambda r:(r['kind'],r['net']),title='по типу и сети')
agg(lambda r:r['day'],title='по дням (все)')
agg(lambda r:(r['day'],r['net']),lambda r:r['kind']=='signal',title='по дням и сети (сигнал)')
agg(lambda r:r['time'][:7],title='по месяцам')
tot=sum(r['pnl'] for r in rows);P('ИТОГО BNB',round(tot,5),'USD~',round(tot*BNB,1))
# signal trades by net: win pct avg
P('## средний pnl% по сети (сигнал)')
d=collections.defaultdict(list)
for r in rows:
    if r['kind']=='signal':d[r['net']].append(f(r['pnl_pct']))
for k in sorted(d):
    v=d[k];P(k,len(v),'avg%',round(sum(v)/len(v),2),'win',sum(x>0 for x in v))
# last signal trade per net
last={}
for r in rows:
    if r['kind']=='signal':last[r['net']]=r['time']
P('последняя сигнальная сделка по сети',last)
# metki in notes
P('## заметки с «метк»/«застр» по сети и дню (сигнал)')
c=collections.Counter()
for r in rows:
    if re.search('метк|застр|сторож',r['note']+r['reason']):c[(r['net'],r['day'],r['kind'])]+=1
for k in sorted(c):P(k,c[k])
# hold_s of signal trades for net 3/4 after 14.11, by pnl
P('## сети 3/4, сигнал: held_s и итог по дням 14-19.11')
d=collections.defaultdict(list)
for r in rows:
    if r['kind']=='signal' and r['net'] in(3,4) and r['day']>='2026-11-13':
        d[(r['net'],r['day'])].append((f(r['held_s']),f(r['pnl_pct']),r['note']))
for k in sorted(d):
    v=d[k];P(k,'n',len(v),'held med',sorted(x[0] for x in v)[len(v)//2],'max',max(x[0] for x in v),'stuck',sum(1 for x in v if x[1]<-50))
# stuck/total loss trades
P('## крупные потери (pnl_pct<-50, сигнал)')
for r in rows:
    if r['kind']=='signal' and f(r['pnl_pct'])<-50:
        P(r['time'],r['net'],r['symbol'],r['stake_bnb'],r['pnl_pct'],r['held_s'],r['note'][:90])
open(r'D:\bot\analysis\review_2311\out_trades.txt','w',encoding='utf-8').write(out.getvalue())
