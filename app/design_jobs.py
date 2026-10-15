"""Bounded single-worker analysis queue; cancellation between kernel calls."""
from concurrent.futures import ThreadPoolExecutor
from threading import Lock, Event
from time import time
from uuid import uuid4
from copy import deepcopy

_pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='design-analysis')
_lock=Lock()
_jobs={}
MAX_JOBS=32

class Cancelled(Exception):pass

def submit(doc_id,revision,fn):
    with _lock:
        for key in list(_jobs):
            if _jobs[key]['state'] in ('done','error','cancelled') and time()-_jobs[key]['created']>3600:del _jobs[key]
        if len(_jobs)>=MAX_JOBS:
            terminal=next((k for k,v in _jobs.items() if v['state'] in ('done','error','cancelled')),None)
            if terminal:del _jobs[terminal]
            else:raise ValueError('Cola de análisis llena')
        key=uuid4().hex;stop=Event()
        _jobs[key]={'id':key,'document':doc_id,'revision':revision,'state':'queued','progress':0,'created':time(),'stop':stop}
    def progress(value,message):
        if stop.is_set():raise Cancelled()
        with _lock:_jobs[key].update(progress=round(value,3),message=message)
    def run():
        try:
            progress(0,'Iniciando')
            with _lock:_jobs[key]['state']='running'
            result=fn(progress);progress(1,'Finalizado')
            with _lock:_jobs[key].update(state='done',result=result)
        except Cancelled:
            with _lock:_jobs[key]['state']='cancelled'
        except Exception as e:
            with _lock:_jobs[key].update(state='error',message=str(e)[:1000])
    _pool.submit(run)
    return read(key)

def read(key,cancel=False):
    with _lock:
        if key not in _jobs:raise KeyError('Trabajo inexistente o expirado')
        row=_jobs[key]
        if cancel and row['state'] in ('queued','running'):row['stop'].set()
        return deepcopy({k:v for k,v in row.items() if k!='stop'})
