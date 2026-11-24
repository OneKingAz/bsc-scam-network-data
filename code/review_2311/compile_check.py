import os
bad=[];n=0
for root in (r'D:\bot',):
    for dp,dns,fns in os.walk(root):
        dns[:]=[d for d in dns if d not in('.venv','__pycache__','backup_before_cleanup','old_data')]
        for fn in fns:
            if fn.endswith('.py'):
                n+=1
                try:compile(open(os.path.join(dp,fn),'rb').read(),fn,'exec')
                except Exception as e:bad.append((os.path.join(dp,fn),str(e)[:80]))
print(n,'py');[print(b) for b in bad]
