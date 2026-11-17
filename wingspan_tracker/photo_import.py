"""Offline photo importer. Requires pillow, opencv-python, pytesseract, Tesseract executable.
Scans are never recorded in games/scores until verified and approved.
"""
import json
import hashlib
import threading
import queue
import os
import re
import shutil
import uuid
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

CATEGORY_KEYS = ['birds', 'bonus_cards', 'end_round_goals', 'eggs', 'cached_food', 'tucked_cards', 'nectar']
ROW_KEYS = CATEGORY_KEYS + ['total']
DEFAULT_NAMES = ['Liv', 'Alex', 'Keaton', 'Aaron']
ALIASES = {'l':'Liv', 'liv':'Liv', 'al':'Alex', 'alex':'Alex', 'k':'Keaton', 'keaton':'Keaton', 'aa':'Aaron', 'a a':'Aaron', 'aaron':'Aaron'}
# Coordinates from a normal full-page photo like the sample supplied: TL, TR, BR, BL.
# If the page is oriented/cropped differently, the user can click replacement corners.
DEFAULT_QUADS = [
    [(0.416,0.133),(0.837,0.146),(0.839,0.452),(0.426,0.455)],
    [(0.433,0.558),(0.851,0.543),(0.862,0.842),(0.440,0.850)],
]


def init_photo_db(db):
    db.executescript('''
      CREATE TABLE IF NOT EXISTS photo_drafts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        image_path TEXT NOT NULL,
        game_on_page INTEGER NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('review','flagged')),
        data_json TEXT NOT NULL,
        issues_json TEXT NOT NULL DEFAULT '[]',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
      );
      CREATE TABLE IF NOT EXISTS photo_settings (
        key TEXT PRIMARY KEY, value TEXT NOT NULL
      );
      INSERT OR IGNORE INTO photo_settings(key,value) VALUES ('auto_import','0');
    ''')
    db.executescript("""
      CREATE TABLE IF NOT EXISTS photo_jobs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        image_path TEXT NOT NULL,
        filename TEXT NOT NULL,
        sha256 TEXT NOT NULL UNIQUE,
        visual_hash TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'queued',
        error TEXT NOT NULL DEFAULT '',
        attempts INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
      );
      CREATE INDEX IF NOT EXISTS ix_photo_jobs_status ON photo_jobs(status);
      CREATE TABLE IF NOT EXISTS photo_seen (sha256 TEXT PRIMARY KEY, filename TEXT NOT NULL, visual_hash TEXT NOT NULL DEFAULT '');
      CREATE TABLE IF NOT EXISTS photo_import_origins (
        draft_id INTEGER, game_on_page INTEGER NOT NULL,
        sha256 TEXT NOT NULL,
        game_id INTEGER UNIQUE,
        PRIMARY KEY(sha256,game_on_page)
      );
    """)
    columns=[r[1] for r in db.execute('PRAGMA table_info(photo_jobs)')]
    if 'visual_hash' not in columns:db.execute("ALTER TABLE photo_jobs ADD COLUMN visual_hash TEXT NOT NULL DEFAULT ''")
    db.execute("INSERT OR IGNORE INTO photo_seen(sha256,filename,visual_hash) SELECT sha256,filename,visual_hash FROM photo_jobs WHERE status='completed'")
    # Any interrupted processing must be resumable.
    db.execute("UPDATE photo_jobs SET status='queued',error='Interrupted; ready to resume' WHERE status='processing'")
    db.commit()


def sha256_file(path):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):
            h.update(chunk)
    return h.hexdigest()


def visual_hash(path):
    from PIL import Image,ImageOps
    with Image.open(path) as im:
        im=ImageOps.exif_transpose(im).convert('L').resize((9,8))
        data=list(im.getdata())
    return ''.join('1' if data[y*9+x]>data[y*9+x+1] else '0' for y in range(8) for x in range(8))

def hash_distance(a,b):
    return sum(x!=y for x,y in zip(a,b)) if len(a)==len(b)==64 else 64

def tesseract_location():
    exe = shutil.which('tesseract')
    if exe:
        return exe
    for base in [os.environ.get('ProgramFiles','C:\\Program Files'),
                 os.environ.get('ProgramFiles(x86)','C:\\Program Files (x86)'),
                 os.environ.get('LOCALAPPDATA','')]:
        if base:
            candidate = Path(base)/'Tesseract-OCR'/'tesseract.exe'
            if candidate.exists():
                return str(candidate)
    return None


def image_to_rows(image_path, quads):
    try:
        import cv2
        import numpy as np
        import pytesseract
        from PIL import Image
    except ImportError as exc:
        raise RuntimeError('Missing Python packages. Run: py -m pip install -r requirements.txt') from exc
    exe=tesseract_location()
    if not exe:
        raise RuntimeError('Tesseract OCR is not installed or not found. Install Tesseract-OCR for Windows, then restart the tracker.')
    pytesseract.pytesseract.tesseract_cmd=exe
    image=cv2.imread(str(image_path))
    if image is None:
        raise RuntimeError('Could not open the photo. Try JPG or PNG.')
    h,w=image.shape[:2]
    drafts=[]
    for game_num, quad in enumerate(quads,1):
        pts=np.float32([(x*w,y*h) for x,y in quad])
        dst=np.float32([[0,0],[800,0],[800,880],[0,880]])
        rect=cv2.warpPerspective(image, cv2.getPerspectiveTransform(pts,dst),(800,880))
        # Each cell is 200px wide; 8 rows plus small margin inside a 880px grid.
        # Margin avoids score sheet rules. Heading/abbreviation is outside the table crop.
        rows=[]; issues=[]
        for col,name in enumerate(DEFAULT_NAMES):
            entry={'player':name, 'values':{}, 'total':None, 'confidence':{}, 'source':'photo'}
            for row_i,key in enumerate(ROW_KEYS):
                y0=int((row_i+0.20)*110); y1=int((row_i+0.81)*110)
                x0=col*200+32; x1=(col+1)*200-25
                crop=rect[y0:y1,x0:x1]
                gray=cv2.cvtColor(crop,cv2.COLOR_BGR2GRAY)
                # Two recognition views: contrast enhanced and binary; accept only agreement.
                gray=cv2.resize(gray,None,fx=2.5,fy=2.5,interpolation=cv2.INTER_CUBIC)
                clahe=cv2.createCLAHE(clipLimit=2.0,tileGridSize=(8,8)).apply(gray)
                variants=[clahe, cv2.threshold(clahe,0,255,cv2.THRESH_BINARY+cv2.THRESH_OTSU)[1]]
                reads=[]
                for variant in variants:
                    txt=pytesseract.image_to_string(variant,config='--psm 10 -c tessedit_char_whitelist=0123456789').strip()
                    match=re.fullmatch(r'\s*(\d{1,3})\s*',txt)
                    reads.append(int(match.group(1)) if match else None)
                if reads[0] is not None and reads[0]==reads[1]:
                    entry['values'][key] = reads[0] if key!='total' else None
                    if key=='total':entry['total']=reads[0]
                    entry['confidence'][key]='agreed'
                else:
                    if key!='total':entry['values'][key]=None
                    entry['confidence'][key]='uncertain'
            # Nectar blank is expected on non-Oceania sheets. Unknown blanks must be verified.
            # We do not guess blank cells as zero, but a completely blank nectar row is 0
            # only for arithmetic validation; the stored nectar category remains NULL.
            missing=[k for k in CATEGORY_KEYS if entry['values'][k] is None and k!='nectar']
            total=entry['total']
            if total is not None and len(missing)==1:
                known=sum((entry['values'][k] or 0) for k in CATEGORY_KEYS)
                implied=total-known
                if implied>=0:
                    entry['values'][missing[0]]=implied
                    entry['confidence'][missing[0]]='inferred'
                    issues.append(f'{name}: {missing[0]} inferred as {implied} (confirm against photo)')
            if total is None:
                issues.append(f'{name}: written total was not confidently read')
            elif all(entry['values'][k] is not None for k in CATEGORY_KEYS if k!='nectar'):
                derived=sum((entry['values'][k] or 0) for k in CATEGORY_KEYS)
                if derived!=total:
                    issues.append(f'{name}: categories total {derived}, written total {total}')
            uncertain=[k for k in ROW_KEYS if entry['confidence'][k]!='agreed']
            if uncertain:
                issues.append(f'{name}: review OCR for '+', '.join(uncertain))
            rows.append(entry)
        # Column labels cannot be verified from numeric OCR. The user confirms them in the calibration step.
        drafts.append({'players':rows,'date':'','notes':f'Photo import, game {game_num}', 'name_confirmed':False,
                       'image_path':str(image_path),'game_on_page':game_num,'issues':issues})
    return drafts


class PhotoImportMixin:
    def setup_photo_database(self):
        init_photo_db(self.db)
        self.photo_folder=Path(__file__).resolve().parent/'photo_imports'
        self.photo_folder.mkdir(exist_ok=True)
        self._photo_events=queue.Queue()
        self._photo_thread=None
        self._photo_pause=threading.Event()
        self._photo_pause.set()
        self._photo_running=False
        self.root.after(250,self._photo_pump)

    def build_photo_tab(self):
        tab=self.photo_tab
        ttk.Label(tab,text='Offline Ollama notebook photo import',style='Header.TLabel').pack(anchor='w',pady=(0,5))
        ttk.Label(tab,text='No corner mapping required. One image can contain two games. All recognized games are saved as drafts first; your official win totals do not change until a draft is approved.',wraplength=1000).pack(anchor='w')
        controls=ttk.Frame(tab);controls.pack(fill='x',pady=10)
        ttk.Button(controls,text='Add Photos to Queue',command=self.photo_choose).pack(side='left',padx=(0,10))
        ttk.Button(controls,text='Start / Resume',command=self.photo_start).pack(side='left',padx=(0,5))
        ttk.Button(controls,text='Pause after current',command=self.photo_pause).pack(side='left',padx=(0,5))
        self.auto_import=tk.BooleanVar(value=self.db.execute("SELECT value FROM photo_settings WHERE key='auto_import'").fetchone()[0]=='1')
        ttk.Checkbutton(controls,text='Auto-import only fully verified games (OFF recommended)',variable=self.auto_import,command=self.save_auto_setting).pack(side='left')
        ttk.Label(tab,text='Safety: AI suggestions always require review and approval in this version. The auto-import setting is saved but cannot bypass approval. Nectar is not applicable.',foreground='#6b7280',wraplength=1000).pack(anchor='w',pady=(0,8))
        self.photo_status=tk.StringVar(value='Ollama mode: qwen3-vl:4b. Keep Ollama running; review required for every game.')
        ttk.Label(tab,textvariable=self.photo_status,foreground='#356b80').pack(anchor='w',pady=(0,8))
        jobframe=ttk.LabelFrame(tab,text='Photo queue — saved between restarts',padding=6);jobframe.pack(fill='both',expand=False,pady=4)
        self.photo_job_tree=ttk.Treeview(jobframe,columns=('id','photo','status','detail'),show='headings',height=5,selectmode='browse')
        for key,title,width in [('id','#',45),('photo','Photo',250),('status','Status',100),('detail','Information',380)]:
            self.photo_job_tree.heading(key,text=title);self.photo_job_tree.column(key,width=width)
        self.photo_job_tree.pack(fill='x')
        jobbuttons=ttk.Frame(jobframe);jobbuttons.pack(fill='x')
        ttk.Button(jobbuttons,text='Retry Selected',command=self.photo_retry_job).pack(side='left')
        ttk.Button(jobbuttons,text='Remove Selected from Queue',command=self.photo_remove_job).pack(side='left',padx=6)
        ttk.Button(jobbuttons,text='Clear Completed Photos',command=self.photo_clear_completed).pack(side='right',padx=6)
        self.photo_progress=tk.StringVar(value='Queue idle')
        ttk.Label(jobframe,textvariable=self.photo_progress).pack(anchor='w')
        panel=ttk.Panedwindow(tab,orient='horizontal');panel.pack(fill='both',expand=True)
        self.queue_trees={}
        for status,title in [('review','Scored games — waiting for approval'),('flagged','Flagged games — corrections required')]:
            frame=ttk.LabelFrame(panel,text=title,padding=8)
            panel.add(frame,weight=1)
            tree=ttk.Treeview(frame,columns=('id','source','game','status'),show='headings',selectmode='browse',height=12)
            for k,label,width in [('id','Draft',54),('source','Photo',235),('game','Game',62),('status','Status',130)]:
                tree.heading(k,text=label);tree.column(k,width=width,anchor='w')
            tree.pack(fill='both',expand=True)
            tree.bind('<Double-1>',lambda event,s=status:self.open_photo_draft(s))
            self.queue_trees[status]=tree
            buttons=ttk.Frame(frame);buttons.pack(fill='x',pady=(8,0))
            ttk.Button(buttons,text='Open / Correct',command=lambda s=status:self.open_photo_draft(s)).pack(side='left')
            ttk.Button(buttons,text='Delete Draft',command=lambda s=status:self.delete_photo_draft(s)).pack(side='right')
            if status=='flagged':
                ttk.Button(buttons,text='Recheck All Flagged (AI)',command=self.recheck_flagged).pack(side='left',padx=6)
        self.refresh_photo_queues()

    def recheck_flagged(self):
        """Reprocess saved flagged images, never official scores. No requeue needed."""
        if getattr(self,'_recheck_active',False):
            messagebox.showinfo('Recheck running','A flagged-draft recheck is already running.');return
        rows=self.db.execute("SELECT id,image_path,game_on_page,data_json FROM photo_drafts WHERE status='flagged' ORDER BY id").fetchall()
        if not rows:
            messagebox.showinfo('No flagged drafts','There are no flagged games to recheck.');return
        if not messagebox.askyesno('Recheck flagged games',
                f'Reanalyze {len(rows)} flagged drafts using their saved photos?\n\n'
                'Only drafts will be updated. Your official games will not change. '
                'This may take some time on your GPU.'):
            return
        self._recheck_active=True
        self._recheck_total=len(rows)
        self._recheck_done=0
        self.photo_status.set(f'Rechecking saved flagged games: 0/{len(rows)}')
        def worker():
            from ollama_reader import read_photo
            cache={}
            for did,path,game_number,old_data_json in rows:
                try:
                    if path not in cache:
                        if not Path(path).is_file():
                            raise FileNotFoundError('Original saved photograph was not found')
                        cache[path]=read_photo(path)
                    candidate=next((x for x in cache[path] if x['game_on_page']==game_number),None)
                    if candidate is None:raise ValueError('AI did not return this game')
                    self._photo_events.put(('recheck_result',did,old_data_json,candidate,None))
                except Exception as exc:
                    self._photo_events.put(('recheck_result',did,old_data_json,None,str(exc)[:400]))
            self._photo_events.put(('recheck_finished',))
        threading.Thread(target=worker,daemon=True).start()

    def _handle_recheck_result(self,event):
        _,did,old_json,candidate,error=event
        self._recheck_done+=1
        if not self.db.execute("SELECT 1 FROM photo_drafts WHERE id=? AND status='flagged'",(did,)).fetchone():return
        if error:
            # Preserve original OCR values and any user edits.
            prior_issues=json.loads(self.db.execute('SELECT issues_json FROM photo_drafts WHERE id=?',(did,)).fetchone()[0])
            with self.db:
                self.db.execute('UPDATE photo_drafts SET issues_json=? WHERE id=?',
                    (json.dumps(prior_issues+['AI recheck failed: '+error]),did))
        else:
            old=json.loads(old_json)
            old_players=old.get('players',[])
            new_players=candidate.get('players',[])
            # Never overwrite saved manual edits or change player assignments automatically.
            # A full re-read only replaces the draft when all original names match,
            # all six categories and written totals are present and reconcile.
            old_names=[p.get('player') for p in old_players]
            new_names=[p.get('player') for p in new_players]
            complete=(len(old_players)==4 and len(new_players)==4 and old_names==new_names
                      and len(set(new_names))==4 and not candidate.get('issues'))
            for player in new_players:
                vals=player.get('values',{})
                if player.get('total') is None or any(vals.get(k) is None for k in CATEGORY_KEYS if k!='nectar'):
                    complete=False
                elif sum(vals[k] for k in CATEGORY_KEYS if k!='nectar')!=player['total']:
                    complete=False
            if complete:
                # Preserve date/notes and original identification; track every difference.
                changes=[]
                for prior,replacement in zip(old_players,new_players):
                    for key in CATEGORY_KEYS:
                        if key=='nectar':continue
                        a=prior.get('values',{}).get(key);b=replacement.get('values',{}).get(key)
                        if a!=b:changes.append(f"{prior['player']} {key}: {a} -> {b}")
                    if prior.get('total')!=replacement.get('total'):
                        changes.append(f"{prior['player']} written total: {prior.get('total')} -> {replacement.get('total')}")
                result=dict(candidate)
                result.pop('issues',None)
                result['date']=old.get('date','')
                result['notes']=old.get('notes','')
                result['name_confirmed']=old.get('name_confirmed',False)
                result['verification_level']='corrected' if changes or candidate.get('auto_corrections') else 'verified'
                result['recheck_changes']=changes
                # Review approval remains mandatory; even matching sums may be misread.
                with self.db:
                    self.db.execute("UPDATE photo_drafts SET data_json=?,issues_json='[]',status='review' WHERE id=?",
                                    (json.dumps(result),did))
            else:
                # Existing fields kept intact; report that independent reread disagreed.
                prior_issues=json.loads(self.db.execute('SELECT issues_json FROM photo_drafts WHERE id=?',(did,)).fetchone()[0])
                issue=prior_issues+['AI reread still ambiguous or inconsistent; existing draft preserved']
                with self.db:
                    self.db.execute('UPDATE photo_drafts SET issues_json=? WHERE id=?',(json.dumps(issue),did))
        self.photo_status.set(f'Rechecking saved flagged games: {self._recheck_done}/{self._recheck_total}')
        self.refresh_photo_queues()

    def save_auto_setting(self):
        with self.db:
            self.db.execute("UPDATE photo_settings SET value=? WHERE key='auto_import'",('1' if self.auto_import.get() else '0',))

    def photo_choose(self):
        paths=filedialog.askopenfilenames(title='Queue scorebook photos',filetypes=[('Images','*.jpg *.jpeg *.png *.bmp *.webp')])
        if not paths:return
        added=0;duplicates=0
        for src in paths:
            src=Path(src)
            try:
                digest=sha256_file(src)
                if self.db.execute('SELECT 1 FROM photo_jobs WHERE sha256=? UNION SELECT 1 FROM photo_seen WHERE sha256=?',(digest,digest)).fetchone():
                    duplicates+=1;continue
                vh=visual_hash(src)
                similar=[(name,h) for name,h in self.db.execute("SELECT filename,visual_hash FROM photo_jobs WHERE visual_hash!=''") if hash_distance(vh,h)<=5]
                similar.extend((name,h) for name,h in self.db.execute("SELECT filename,visual_hash FROM photo_seen WHERE visual_hash!=''") if hash_distance(vh,h)<=5)
                if similar and not messagebox.askyesno('Possible duplicate page',f'{src.name} looks similar to {similar[0][0]}.\n\nAdd it to the queue anyway?'):
                    duplicates+=1;continue
                dest=self.photo_folder/(digest+src.suffix.lower())
                if not dest.exists():shutil.copy2(src,dest)
                with self.db:
                    self.db.execute('INSERT INTO photo_jobs(image_path,filename,sha256,visual_hash) VALUES(?,?,?,?)',(str(dest),src.name,digest,vh))
                added+=1
            except Exception as exc:
                messagebox.showwarning('Photo not queued',f'{src.name}: {exc}')
        self.refresh_photo_queues()
        self.photo_status.set(f'Queued {added} new photos; skipped {duplicates} identical files. Click Start / Resume.')

    def photo_start(self):
        self._photo_pause.clear()
        if self._photo_thread and self._photo_thread.is_alive():return
        self._photo_thread=threading.Thread(target=self._photo_worker,daemon=True)
        self._photo_thread.start()
        self.photo_status.set('Processing photos in background; you can still use the tracker.')

    def photo_pause(self):
        self._photo_pause.set()
        self.photo_status.set('Pausing after the current photo finishes...')

    def _photo_worker(self):
        # No SQLite operations from background thread. UI thread owns the database.
        while not self._photo_pause.is_set():
            answer=queue.Queue(maxsize=1)
            self._photo_events.put(('request_job',answer))
            try:job=answer.get(timeout=15)
            except queue.Empty:break
            if job is None:break
            job_id,path=job
            try:
                from ollama_reader import read_photo
                result=read_photo(path)
                self._photo_events.put(('success',job_id,result))
            except Exception as exc:
                self._photo_events.put(('failure',job_id,str(exc)))
            # Wait for commit before starting another file.
            ack=queue.Queue(maxsize=1)
            self._photo_events.put(('sync',ack))
            try:ack.get(timeout=15)
            except queue.Empty:break
        self._photo_events.put(('finished',))

    def _photo_pump(self):
        try:
            for _ in range(10):
                try:event=self._photo_events.get_nowait()
                except queue.Empty:break
                op=event[0]
                if op=='request_job':
                    answer=event[1]
                    row=self.db.execute("SELECT id,image_path FROM photo_jobs WHERE status='queued' ORDER BY id LIMIT 1").fetchone()
                    if row:
                        with self.db:self.db.execute("UPDATE photo_jobs SET status='processing',attempts=attempts+1,updated_at=CURRENT_TIMESTAMP WHERE id=?",(row[0],))
                    answer.put(row)
                    self.refresh_photo_queues()
                elif op=='success':
                    _,jid,drafts=event
                    job=self.db.execute('SELECT image_path,sha256 FROM photo_jobs WHERE id=?',(jid,)).fetchone()
                    if job:
                        try:
                            with self.db:
                                for data in drafts:
                                    num=data['game_on_page']
                                    if self.db.execute('SELECT 1 FROM photo_import_origins WHERE sha256=? AND game_on_page=?',(job[1],num)).fetchone():continue
                                    issues=data.pop('issues')
                                    data['verification_level']='manual' if issues else ('corrected' if data.get('auto_corrections') else 'verified')
                                    cur=self.db.execute('INSERT INTO photo_drafts(image_path,game_on_page,status,data_json,issues_json) VALUES(?,?,?,?,?)',(job[0],num,'flagged' if issues else 'review',json.dumps(data),json.dumps(issues)))
                                    self.db.execute('INSERT INTO photo_import_origins(draft_id,game_on_page,sha256) VALUES(?,?,?)',(cur.lastrowid,num,job[1]))
                                self.db.execute("UPDATE photo_jobs SET status='completed',error='',updated_at=CURRENT_TIMESTAMP WHERE id=?",(jid,))
                        except Exception as exc:
                            with self.db:self.db.execute("UPDATE photo_jobs SET status='failed',error=? WHERE id=?",(str(exc)[:500],jid))
                elif op=='failure':
                    _,jid,error=event
                    with self.db:self.db.execute("UPDATE photo_jobs SET status='failed',error=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",(error[:500],jid))
                elif op=='recheck_result':self._handle_recheck_result(event)
                elif op=='recheck_finished':
                    self._recheck_active=False
                    self.photo_status.set('Flagged recheck finished. Review promoted drafts; unresolved games remain flagged.')
                elif op=='sync':event[1].put(True)
                elif op=='finished':
                    self.photo_status.set('Queue stopped or finished. Review the completed drafts and any failed photos.')
                self.refresh_photo_queues()
        finally:
            try:self.root.after(250,self._photo_pump)
            except tk.TclError:pass

    def photo_retry_job(self):
        selection=self.photo_job_tree.selection()
        if not selection:return
        jid=int(selection[0]);row=self.db.execute('SELECT status FROM photo_jobs WHERE id=?',(jid,)).fetchone()
        if row and row[0]=='failed':
            with self.db:self.db.execute("UPDATE photo_jobs SET status='queued',error='' WHERE id=?",(jid,))
        self.refresh_photo_queues()

    def photo_remove_job(self):
        selection=self.photo_job_tree.selection()
        if not selection:return
        jid=int(selection[0]);row=self.db.execute('SELECT status FROM photo_jobs WHERE id=?',(jid,)).fetchone()
        if not row or row[0]=='processing':
            messagebox.showwarning('Cannot remove','Pause and allow the current image to finish first.');return
        if not messagebox.askyesno('Remove photo from queue','Remove this queue item? Existing drafts and official games will remain unchanged.'):return
        with self.db:self.db.execute('DELETE FROM photo_jobs WHERE id=?',(jid,))
        self.refresh_photo_queues()

    def photo_clear_completed(self):
        count=self.db.execute("SELECT COUNT(*) FROM photo_jobs WHERE status='completed'").fetchone()[0]
        if not count:
            messagebox.showinfo('Queue is clear','There are no completed photos to clear.')
            return
        if not messagebox.askyesno('Clear completed photos',
                f'Remove {count} completed photo queue entries?\n\nExisting review drafts, flagged drafts, photos, duplicate fingerprints and approved games will be preserved.'):
            return
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO photo_seen(sha256,filename,visual_hash) SELECT sha256,filename,visual_hash FROM photo_jobs WHERE status='completed'")
            self.db.execute("DELETE FROM photo_jobs WHERE status='completed'")
        self.refresh_photo_queues()
        messagebox.showinfo('Queue cleared',f'Removed {count} completed queue entries. Your drafts and game history are unchanged.')

    def import_photo_automatically(self, data):
        players=data['players']
        if not data.get('name_confirmed') or len(players)<2:return False
        seen=set();out=[]
        for p in players:
            name=p['player'];pid=self.name_to_id(name)
            if pid is None or pid in seen or p['total'] is None:return False
            seen.add(pid)
            if any(p['confidence'].get(k)!='agreed' for k in ROW_KEYS):return False
            values=p['values']
            if any(values[k] is None for k in CATEGORY_KEYS if k!='nectar'):return False
            if sum(v or 0 for v in values.values())!=p['total']:return False
            out.append((pid,values,p['total']))
        # Never automatically insert an exact duplicate.
        signature=sorted((pid,tuple(vals[k] for k in CATEGORY_KEYS),total) for pid,vals,total in out)
        for (gid,) in self.db.execute('SELECT id FROM games'):
            previous=[]
            for record in self.db.execute('SELECT player_id,birds,bonus_cards,end_round_goals,eggs,cached_food,tucked_cards,nectar,total FROM scores WHERE game_id=?',(gid,)):
                previous.append((record[0],tuple(record[1:8]),record[8]))
            if sorted(previous)==signature:return False
        cur=self.db.execute('INSERT INTO games(played_on,notes) VALUES (?,?)',(data.get('date') or None,data.get('notes','Auto-verified photo import')))
        for pid,vals,total in out:
            self.db.execute('INSERT INTO scores(game_id,player_id,birds,bonus_cards,end_round_goals,eggs,cached_food,tucked_cards,nectar,total) VALUES (?,?,?,?,?,?,?,?,?,?)',(cur.lastrowid,pid,*[vals[k] for k in CATEGORY_KEYS],total))
        return True

    def calibrate_photo(self, path):
        """Set the four corners of each game's NUMERIC GRID (including total row)."""
        from PIL import Image, ImageTk
        im=Image.open(path).convert('RGB'); w,h=im.size
        scale=min(900/w,720/h,1.0)
        preview=im.resize((int(w*scale),int(h*scale)))
        win=tk.Toplevel(self.root);win.title('Select score grid corners — '+path.name)
        win.grab_set()
        ttk.Label(win,text='Click 4 corners for TOP game, then 4 for BOTTOM game: top-left, top-right, bottom-right, bottom-left. Include the handwritten TOTAL row.').pack(padx=8,pady=6)
        canvas=tk.Canvas(win,width=preview.width,height=preview.height)
        canvas.pack(padx=8)
        win.image_ref=ImageTk.PhotoImage(preview)
        canvas.create_image(0,0,image=win.image_ref,anchor='nw')
        coords=[];result=[None]
        # Show guide dots based on the previously provided typical full-page sample.
        for q in DEFAULT_QUADS:
            for x,y in q:
                px,py=x*preview.width,y*preview.height
                canvas.create_oval(px-4,py-4,px+4,py+4,outline='#00bbdd',width=2,tags='guide')
        instruct=tk.StringVar(value='Click corner 1 of 8 — or choose Use Sample Layout')
        ttk.Label(win,textvariable=instruct).pack(pady=3)
        def click(e):
            if len(coords)>=8:return
            x=e.x/preview.width;y=e.y/preview.height
            coords.append((x,y));canvas.create_oval(e.x-5,e.y-5,e.x+5,e.y+5,fill='red',outline='white',tags='selected')
            instruct.set(f'{len(coords)} of 8 corners selected' if len(coords)<8 else 'Ready to read the two games')
        canvas.bind('<Button-1>',click)
        def accept():
            if len(coords)!=8:
                messagebox.showwarning('Corners missing','Please click all 8 corners or use the sample layout.',parent=win);return
            result[0]=[coords[:4],coords[4:]];win.destroy()
        def preset():result[0]=DEFAULT_QUADS;win.destroy()
        buttons=ttk.Frame(win);buttons.pack(pady=8)
        ttk.Button(buttons,text='Use Sample Layout',command=preset).pack(side='left',padx=6)
        ttk.Button(buttons,text='Reset Clicks',command=lambda:(coords.clear(),canvas.delete('selected'),instruct.set('Click corner 1 of 8'))).pack(side='left',padx=6)
        ttk.Button(buttons,text='Use My 8 Corners',command=accept).pack(side='left',padx=6)
        ttk.Button(buttons,text='Cancel',command=win.destroy).pack(side='left',padx=6)
        win.wait_window()
        self.photo_names_confirmed=False
        if result[0] is not None:
            self.photo_names_confirmed=messagebox.askyesno('Confirm player order','Are all four columns in BOTH games ordered Liv, Alex, Keaton, Aaron?\n\nChoose No if any order differs; you can assign the correct players in the review screen.')
        return result[0]

    def refresh_photo_queues(self):
        if hasattr(self,'photo_job_tree'):
            tree=self.photo_job_tree
            tree.delete(*tree.get_children())
            for jid,name,status,error in self.db.execute('SELECT id,filename,status,error FROM photo_jobs ORDER BY id DESC LIMIT 300'):
                tree.insert('','end',iid=str(jid),values=(jid,name,status,error[:85]))
            counts=dict(self.db.execute('SELECT status,COUNT(*) FROM photo_jobs GROUP BY status'))
            self.photo_progress.set('  |  '.join(f'{k}: {counts.get(k,0)}' for k in ('queued','processing','completed','failed')))
        if not hasattr(self,'queue_trees'):return
        for status,tree in self.queue_trees.items():
            tree.delete(*tree.get_children())
            for draft_id,source,number in self.db.execute('SELECT id,image_path,game_on_page FROM photo_drafts WHERE status=? ORDER BY id DESC',(status,)):
                level_row=self.db.execute('SELECT data_json FROM photo_drafts WHERE id=?',(draft_id,)).fetchone()
                level=json.loads(level_row[0]).get('verification_level') if level_row else None
                desc=({'verified':'Verified — review','corrected':'Corrected — review','manual':'Manual review'}).get(level,'Ready for review' if status=='review' else 'Needs correction')
                tree.insert('','end',iid=str(draft_id),values=(draft_id,Path(source).name,number,desc))

    def selected_photo_draft(self,status):
        sel=self.queue_trees[status].selection()
        if not sel:messagebox.showwarning('Select a draft','Choose a game from the queue.');return None
        return int(sel[0])

    def delete_photo_draft(self,status):
        did=self.selected_photo_draft(status)
        if did is None:return
        if messagebox.askyesno('Delete draft','Delete this draft? This will not delete any official saved games.'):
            with self.db:
                self.db.execute('DELETE FROM photo_import_origins WHERE draft_id=? AND game_id IS NULL',(did,))
                self.db.execute('DELETE FROM photo_drafts WHERE id=?',(did,))
            self.refresh_photo_queues()

    def open_photo_draft(self,status):
        did=self.selected_photo_draft(status)
        if did is None:return
        path,number,data_str,issues_str=self.db.execute('SELECT image_path,game_on_page,data_json,issues_json FROM photo_drafts WHERE id=?',(did,)).fetchone()
        data=json.loads(data_str)
        win=tk.Toplevel(self.root);win.title(f'Review photo draft #{did} — game {number}')
        win.geometry('1180x690');win.minsize(920,550)
        heading=ttk.Frame(win,padding=10);heading.pack(fill='x')
        ttk.Label(heading,text=f'Photo game {number} — verify every player and score',font=('Segoe UI',13,'bold')).pack(anchor='w')
        ttk.Label(heading,text='Recognition suggestions are NOT confirmed facts. Compare against the photo, correct mistakes, then press Validate / Approve.',foreground='#a04b16').pack(anchor='w')
        main=ttk.Panedwindow(win,orient='horizontal');main.pack(fill='both',expand=True,padx=10)
        left=ttk.Frame(main);main.add(left,weight=2)
        right=ttk.Frame(main);main.add(right,weight=3)
        # Full-resolution, scrollable image reviewer. The cropped view is only a
        # convenience: user can switch to Full Page and zoom out/in at any time.
        from PIL import Image, ImageTk, ImageOps
        try:
            source_image=ImageOps.exif_transpose(Image.open(path)).convert('RGB')
        except Exception as exc:
            messagebox.showerror('Photo unavailable',f'Could not open source photo: {exc}',parent=win)
            win.destroy()
            return
        photo_toolbar=ttk.Frame(left);photo_toolbar.pack(fill='x',pady=(3,5))
        ttk.Label(photo_toolbar,text='Original score sheet',font=('Segoe UI',10,'bold')).pack(side='left')
        photo_frame=ttk.Frame(left)
        photo_frame.pack(fill='both',expand=True)
        photo_canvas=tk.Canvas(photo_frame,background='#e7eee3',highlightthickness=0)
        photo_vscroll=ttk.Scrollbar(photo_frame,orient='vertical',command=photo_canvas.yview)
        photo_hscroll=ttk.Scrollbar(photo_frame,orient='horizontal',command=photo_canvas.xview)
        photo_canvas.configure(xscrollcommand=photo_hscroll.set,yscrollcommand=photo_vscroll.set)
        photo_frame.grid_rowconfigure(0,weight=1);photo_frame.grid_columnconfigure(0,weight=1)
        photo_canvas.grid(row=0,column=0,sticky='nsew')
        photo_vscroll.grid(row=0,column=1,sticky='ns')
        photo_hscroll.grid(row=1,column=0,sticky='ew')
        photo_state={'mode':'Game only','zoom':1.0,'image':None,'tk_image':None}

        def current_photo():
            if photo_state['mode']=='Full page':
                return source_image
            w,h=source_image.size
            # Loose automatic crop; deliberately retain the entire scoring area
            # and let the user switch to full page for unusually framed photos.
            if number==1:
                return source_image.crop((0,0,w,int(h*0.57)))
            return source_image.crop((0,int(h*0.42),w,h))

        def redraw_photo(reset=False):
            view=current_photo()
            photo_canvas.update_idletasks()
            cw=max(360,photo_canvas.winfo_width()-12)
            ch=max(340,photo_canvas.winfo_height()-12)
            fit=min(cw/view.width,ch/view.height)
            scaled=max(0.05,fit*photo_state['zoom'])
            iw=max(1,round(view.width*scaled));ih=max(1,round(view.height*scaled))
            interpolation=Image.Resampling.LANCZOS if scaled<1 else Image.Resampling.BICUBIC
            resized=view.resize((iw,ih),interpolation)
            photo_state['tk_image']=ImageTk.PhotoImage(resized,master=win)
            photo_canvas.delete('all')
            photo_canvas.create_image(0,0,anchor='nw',image=photo_state['tk_image'])
            photo_canvas.configure(scrollregion=(0,0,iw,ih))
            if reset:
                photo_canvas.xview_moveto(0);photo_canvas.yview_moveto(0)
            zoom_text.set(f'{round(photo_state["zoom"]*100)}%')

        def change_zoom(factor):
            photo_state['zoom']=max(0.4,min(5.0,photo_state['zoom']*factor))
            redraw_photo()

        def set_photo_mode(mode):
            photo_state['mode']=mode
            photo_state['zoom']=1.0
            redraw_photo(reset=True)

        view_mode=tk.StringVar(value='Game only')
        mode_choice=ttk.Combobox(photo_toolbar,textvariable=view_mode,values=['Game only','Full page'],state='readonly',width=11)
        mode_choice.pack(side='left',padx=5)
        mode_choice.bind('<<ComboboxSelected>>',lambda ev:set_photo_mode(view_mode.get()))
        zoom_text=tk.StringVar(value='100%')
        ttk.Button(photo_toolbar,text='−',width=3,command=lambda:change_zoom(1/1.25)).pack(side='left',padx=2)
        ttk.Label(photo_toolbar,textvariable=zoom_text,width=5,anchor='center').pack(side='left')
        ttk.Button(photo_toolbar,text='+',width=3,command=lambda:change_zoom(1.25)).pack(side='left',padx=2)
        ttk.Button(photo_toolbar,text='Fit',command=lambda:(photo_state.update(zoom=1.0),redraw_photo(reset=True))).pack(side='left',padx=4)
        # Mouse wheel to zoom, drag to pan. Scrollbars support navigating any crop.
        photo_canvas.bind('<MouseWheel>',lambda ev:change_zoom(1.2 if ev.delta>0 else 1/1.2))
        photo_canvas.bind('<Button-4>',lambda ev:change_zoom(1.2))
        photo_canvas.bind('<Button-5>',lambda ev:change_zoom(1/1.2))
        photo_canvas.bind('<ButtonPress-1>',lambda ev:photo_canvas.scan_mark(ev.x,ev.y))
        photo_canvas.bind('<B1-Motion>',lambda ev:photo_canvas.scan_dragto(ev.x,ev.y,gain=1))
        photo_canvas.bind('<Configure>',lambda ev:redraw_photo() if photo_state['tk_image'] is None else None)
        ttk.Label(left,text='Mouse wheel: zoom • Drag: pan • Switch to Full page if needed',wraplength=400).pack(pady=(3,0))
        win.after(80,lambda:redraw_photo(reset=True))
        ttk.Label(right,text='Scores by category (blank = unreadable; check the photo)',wraplength=600).pack(anchor='w')
        issues=json.loads(issues_str)
        corrections=data.get('auto_corrections') or []
        if data.get('recheck_changes'):
            ttk.Label(right,text='AI recheck proposed changes (confirm against photo): '+ '; '.join(data['recheck_changes'][:12]),
                      wraplength=640,foreground='#9e4f1e').pack(anchor='w',pady=4)
        if corrections:
            ttk.Label(right,text='Automatic 6 -> 0 suggestion(s) — verify against photo: '+ '; '.join(corrections),
                      wraplength=640,foreground='#9e4f1e').pack(anchor='w',pady=4)
        if issues:
            ttk.Label(right,text='AI flags: '+'; '.join(issues[:5]),wraplength=640,foreground='#a04b16').pack(anchor='w',pady=4)
        grid=ttk.Frame(right);grid.pack(fill='x',pady=8)
        header=['Player']+['Birds','Bonus','Goals','Eggs','Food','Tucked','Nectar','Written total']
        for c,title in enumerate(header):ttk.Label(grid,text=title,font=('Segoe UI',9,'bold')).grid(row=0,column=c,padx=2)
        names=[];entries=[]
        players=[n for _,n in self.db.execute('SELECT id,name FROM players ORDER BY name')]
        for r,item in enumerate(data['players'],1):
            name=tk.StringVar(value=item['player']);names.append(name)
            ttk.Combobox(grid,values=players,textvariable=name,width=10,state='readonly').grid(row=r,column=0,padx=2,pady=7)
            cells={}
            for c,key in enumerate(ROW_KEYS,1):
                original = item['total'] if key=='total' else item['values'].get(key)
                v=tk.StringVar(value='' if original is None else str(original));cells[key]=v
                e=ttk.Entry(grid,textvariable=v,width=7, state='disabled' if key=='nectar' else 'normal')
                e.grid(row=r,column=c,padx=2)
                if item.get('confidence',{}).get(key)!='agreed':
                    e.configure(style='Review.TEntry')
                e.bind('<Return>',lambda ev: (ev.widget.tk_focusNext().focus_set(),'break')[1])
            entries.append(cells)
        style=ttk.Style();style.configure('Review.TEntry',fieldbackground='#fff0c9')
        ttk.Label(right,text='Date (optional, YYYY-MM-DD)').pack(anchor='w',pady=(7,0))
        day=tk.StringVar(value=data.get('date',''));ttk.Entry(right,textvariable=day,width=20).pack(anchor='w')
        ttk.Label(right,text='Notes').pack(anchor='w',pady=(7,0))
        notes=tk.StringVar(value=data.get('notes',''));ttk.Entry(right,textvariable=notes,width=65).pack(anchor='w',fill='x')
        ttk.Label(right,text='Live score checks (calculated vs. written total)',font=('Segoe UI',9,'bold')).pack(anchor='w',pady=(10,0))
        checks=tk.StringVar(value='')
        ttk.Label(right,textvariable=checks,wraplength=650).pack(anchor='w',pady=3)
        def update_score_checks(*_):
            parts=[]
            for i,cells in enumerate(entries):
                vals=[cells[k].get().strip() for k in CATEGORY_KEYS if k!='nectar']
                total=cells['total'].get().strip()
                if all(v.isdecimal() for v in vals) and total.isdecimal():
                    actual=sum(map(int,vals));symbol='✓' if actual==int(total) else '⚠'
                    parts.append(f'{names[i].get()}: {actual}/{total} {symbol}')
                else:
                    parts.append(f'{names[i].get()}: incomplete')
            checks.set('   |   '.join(parts))
        for cells in entries:
            for key,v in cells.items():
                if key!='nectar':v.trace_add('write',update_score_checks)
        update_score_checks()
        validation=tk.StringVar(value='Review every field. Highlighted entries were uncertain, blank, or inferred.')
        ttk.Label(right,textvariable=validation,wraplength=610,foreground='#9e4f1e').pack(anchor='w',pady=14)

        def collect():
            output=[];seen=set()
            for i,cells in enumerate(entries):
                name=names[i].get().strip()
                if name not in players:raise ValueError(f'Row {i+1}: choose a valid player name.')
                if name.casefold() in seen:raise ValueError(f'{name} appears more than once.')
                seen.add(name.casefold())
                vals={}
                for key in CATEGORY_KEYS:
                    raw=cells[key].get().strip()
                    if raw and (not raw.isdecimal()):raise ValueError(f'{name}: {key} must be a non-negative integer.')
                    vals[key]=None if key=='nectar' else (int(raw) if raw else None)
                totalraw=cells['total'].get().strip()
                if not totalraw.isdecimal():raise ValueError(f'{name}: written total is required.')
                total=int(totalraw)
                # Nectar optional; require all six ordinary categories for unambiguous validation.
                missing=[k for k in CATEGORY_KEYS if k!='nectar' and vals[k] is None]
                if missing:raise ValueError(f'{name}: fill in {", ".join(missing)} (or 0 if blank on the score sheet).')
                if sum(v or 0 for v in vals.values())!=total:
                    raise ValueError(f'{name}: categories sum to {sum(v or 0 for v in vals.values())}, but written total is {total}.')
                output.append((name,vals,total))
            if len(output)<2:raise ValueError('A game requires at least two players.')
            txt=day.get().strip()
            if txt:
                from datetime import date
                if date.fromisoformat(txt).isoformat()!=txt:raise ValueError('Date must be YYYY-MM-DD.')
            return output

        def persist_draft(status_value,issues):
            changed={'players':[],'date':day.get().strip(),'notes':notes.get().strip(),'name_confirmed':True,'image_path':path,'game_on_page':number}
            for i,cells in enumerate(entries):
                changed['players'].append({'player':names[i].get(), 'values':{k:(None if k=='nectar' else (int(cells[k].get()) if cells[k].get().isdecimal() else None)) for k in CATEGORY_KEYS}, 'total':int(cells['total'].get()) if cells['total'].get().isdecimal() else None})
            with self.db:
                self.db.execute('UPDATE photo_drafts SET data_json=?,issues_json=?,status=? WHERE id=?',(json.dumps(changed),json.dumps(issues),status_value,did))
            self.refresh_photo_queues()

        def verify():
            try:collect()
            except (ValueError,OverflowError) as exc:
                validation.set('Not ready: '+str(exc));persist_draft('flagged',[str(exc)]);return
            validation.set('All four players and category totals match. Ready for approval.')
            persist_draft('review',[])

        def approve():
            try:rows=collect()
            except (ValueError,OverflowError) as exc:
                validation.set('Cannot import: '+str(exc));persist_draft('flagged',[str(exc)]);return
            # Duplicate guard: identical players, scores and all categories, ignoring date.
            signature=sorted((name.casefold(),tuple(vals[k] for k in CATEGORY_KEYS),total) for name,vals,total in rows)
            for (gid,) in self.db.execute('SELECT id FROM games'):
                other=[]
                for record in self.db.execute('SELECT p.name,s.birds,s.bonus_cards,s.end_round_goals,s.eggs,s.cached_food,s.tucked_cards,s.nectar,s.total FROM scores s JOIN players p ON p.id=s.player_id WHERE s.game_id=?',(gid,)):
                    other.append((record[0].casefold(),tuple(record[1:8]),record[8]))
                if sorted(other)==signature:
                    if not messagebox.askyesno('Possible duplicate',f'This exact set of scores already exists as game #{gid}. Import anyway?',parent=win):return
                    break
            if not messagebox.askyesno('Final approval',f'Add this game with {len(rows)} players to the OFFICIAL database?',parent=win):return
            with self.db:
                cur=self.db.execute('INSERT INTO games(played_on,notes) VALUES(?,?)',(day.get().strip() or None,notes.get().strip()))
                gid=cur.lastrowid
                for name,vals,total in rows:
                    pid=self.name_to_id(name)
                    self.db.execute('INSERT INTO scores(game_id,player_id,birds,bonus_cards,end_round_goals,eggs,cached_food,tucked_cards,nectar,total) VALUES (?,?,?,?,?,?,?,?,?,?)',(gid,pid,*[vals[k] for k in CATEGORY_KEYS],total))
                self.db.execute('UPDATE photo_import_origins SET game_id=?,draft_id=NULL WHERE draft_id=?',(gid,did))
                self.db.execute('DELETE FROM photo_drafts WHERE id=?',(did,))
            self.refresh_photo_queues();self.refresh_all();win.destroy()
            messagebox.showinfo('Imported',f'Game #{gid} is now recorded in your official Wingspan history.')
        actions=ttk.Frame(right);actions.pack(fill='x',pady=10)
        ttk.Button(actions,text='Save Corrections / Validate',command=verify).pack(side='left',padx=4)
        ttk.Button(actions,text='Approve and Import Game',command=approve).pack(side='left',padx=4)
        ttk.Button(actions,text='Close',command=win.destroy).pack(side='right',padx=4)
