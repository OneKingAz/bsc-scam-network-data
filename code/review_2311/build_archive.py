import os,re,sys,zipfile,time,json
OUT=r'C:\Users\IT\Desktop\bsc_meme_bot\bsc_meme_bot_full_2026-11-23.zip'
LOG=r'D:\bot\analysis\review_2311\secret_scan.log'
ROOTS=[(r'D:\bot','D_bot'),(r'C:\Users\IT\Desktop\bsc_meme_bot','Desktop_bsc_meme_bot')]
SKIP_DIRS={'.venv','__pycache__','.claude','venv'}
NAME_EXCL=lambda n: (n.lower().endswith('.env') or n.lower()=='.env' or re.match(r'(?i)^config.*\.json$',n) or n.lower() in('api telegram.txt','state_live.json','nodekey','sol_wallet.env','lp_wallet.env','hop_wallets.env'))
BIN_EXT={'.exe','.dll','.pyd','.so','.dat','.pdb'}
TEXT_EXT={'.py','.md','.txt','.cmd','.ps1','.json','.jsonl','.csv','.log','.html','.go','.js','.url','.ini','.yml','.yaml','.sol','.hex','.out','.err','.flag','.iss','.mod','.sum','.sample',''}
pats={
 'telegram_token':re.compile(rb'\b\d{8,11}:AA[A-Za-z0-9_-]{30,}'),
 'key_assign':re.compile(rb'(?i)\b[A-Z0-9_]*(?:PRIVATE|SECRET|PASSWORD|PASSWD|MNEMONIC|SEED|API[_-]?KEY|_KEY)[A-Z0-9_]*["\']?\s*[=:]\s*["\']?[A-Za-z0-9+/_\-]{24,}'),
 'hex64_ctx':re.compile(rb'(?i)(?:priv|secret|seed)[^\n]{0,40}\b(?:0x)?[0-9a-f]{64}\b'),
 'url_key':re.compile(rb'(?i)(?:api-key|apikey|api_key)=[A-Za-z0-9\-]{16,}|nodereal\.io/[^\s"\']*?/[0-9a-f]{32}|ws/v1/[0-9a-f]{20,}'),
 'pem':re.compile(rb'-----BEGIN [A-Z ]*PRIVATE KEY-----'),
 'sol_array':re.compile(rb'\[(?:\s*\d{1,3}\s*,){63}\s*\d{1,3}\s*\]'),
 'b58_ctx':re.compile(rb'(?i)(?:private|secret|keypair|SOL_KEY)[^\n]{0,30}[1-9A-HJ-NP-Za-km-z]{86,88}'),
}
HOT=(b'key',b'secret',b'priv',b'seed',b'pass',b'mnemonic',b'api',b':aa',b'begin',b'nodereal',b'ws/v1')
def scan(path):
    with open(path,'rb') as f:
        for ln,line in enumerate(f,1):
            low=line.lower()
            if any(h in low for h in HOT):
                for n,p in pats.items():
                    if n=='sol_array':continue
                    if p.search(line):return n,ln
            if b'[' in line and line.count(b',')>=63 and len(line)<5000:
                if pats['sol_array'].search(line):return 'sol_array',ln
    return None
excl=[];hits=[];nfiles=0;nbytes=0
log=open(LOG,'w',encoding='utf-8')
z=zipfile.ZipFile(OUT,'w',zipfile.ZIP_DEFLATED,compresslevel=4,allowZip64=True)
t0=time.time()
for root,pref in ROOTS:
    for dp,dns,fns in os.walk(root):
        dns[:]=[d for d in dns if d not in SKIP_DIRS]
        for fn in fns:
            p=os.path.join(dp,fn)
            if os.path.abspath(p)==os.path.abspath(OUT):continue
            ext=os.path.splitext(fn)[1].lower()
            rel=os.path.join(pref,os.path.relpath(p,root))
            if NAME_EXCL(fn):
                excl.append((rel,'по имени'));log.write(f'EXCL-NAME {rel}\n');continue
            if ext in BIN_EXT:
                excl.append((rel,'бинарник'));log.write(f'EXCL-BIN {rel}\n');continue
            try:
                if ext in TEXT_EXT:
                    h=scan(p)
                    if h:
                        excl.append((rel,f'шаблон секрета {h[0]} стр.{h[1]}'));log.write(f'EXCL-SECRET {rel} {h[0]} line {h[1]}\n');log.flush();continue
                z.write(p,rel);nfiles+=1;nbytes+=os.path.getsize(p)
            except Exception as e:
                log.write(f'ERR {rel} {e}\n')
    log.write(f'ROOT done {root} {time.time()-t0:.0f}s files={nfiles}\n');log.flush()
z.close()
json.dump({'files':nfiles,'bytes':nbytes,'excl':excl},open(r'D:\bot\analysis\review_2311\archive_stats.json','w',encoding='utf-8'),ensure_ascii=False)
log.write(f'DONE {time.time()-t0:.0f}s files={nfiles} bytes={nbytes} zip={os.path.getsize(OUT)}\n');log.close()
