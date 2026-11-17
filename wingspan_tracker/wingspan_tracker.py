"""Wingspan Score Tracker – Tkinter + SQLite + Matplotlib."""
import sqlite3
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from datetime import date, datetime
from collections import Counter, defaultdict
from pathlib import Path
import csv
from photo_import import PhotoImportMixin

try:
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    from matplotlib.figure import Figure
except ImportError:
    FigureCanvasTkAgg = None
    Figure = None

DB_PATH = Path(__file__).resolve().parent / 'wingspan_scores.db'
DEFAULT_PLAYERS = ('Liv', 'Alex', 'Keaton', 'Aaron')
COLOR_OPTIONS = {'Yellow':'#e0ad00', 'Red':'#d62728', 'Green':'#2ca02c', 'Purple':'#8b5cf6', 'Blue':'#1f77b4', 'Orange':'#ff7f0e', 'Pink':'#e75480', 'Teal':'#008b8b', 'Black':'#333333'}
DEFAULT_COLORS = {'liv':'Yellow','alex':'Red','keaton':'Green','aaron':'Purple'}

CATEGORIES = [('birds','Birds'),('bonus_cards','Bonus Cards'),('end_round_goals','End-of-Round Goals'),
              ('eggs','Eggs'),('cached_food','Cached Food'),('tucked_cards','Tucked Cards'),('nectar','Nectar')]

class App(PhotoImportMixin):
    def __init__(self, root):
        self.root = root
        root.title('Wingspan Score Tracker')
        root.geometry('1140x750')
        root.minsize(900,600)
        self.db = sqlite3.connect(DB_PATH)
        self.db.execute('PRAGMA foreign_keys=ON')
        self.initialize_db()
        self.setup_photo_database()
        self.player_lookup = dict(self.db.execute('SELECT id,name FROM players ORDER BY name'))
        self.rows = []
        self.editing_game = None
        self.build_ui()
        self.refresh_all()
        root.protocol('WM_DELETE_WINDOW', self.close)

    def initialize_db(self):
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS players (
          id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE
        );
        CREATE TABLE IF NOT EXISTS games (
          id INTEGER PRIMARY KEY, played_on TEXT, notes TEXT NOT NULL DEFAULT '',
          entered_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS scores (
          game_id INTEGER NOT NULL REFERENCES games(id) ON DELETE CASCADE,
          player_id INTEGER NOT NULL REFERENCES players(id),
          birds INTEGER, bonus_cards INTEGER, end_round_goals INTEGER,
          eggs INTEGER, cached_food INTEGER, tucked_cards INTEGER, nectar INTEGER,
          total INTEGER NOT NULL CHECK(total >= 0),
          PRIMARY KEY (game_id, player_id)
        );
        CREATE INDEX IF NOT EXISTS ix_scores_player ON scores(player_id);
        ''')
        self.db.execute('''CREATE TABLE IF NOT EXISTS player_preferences (
            player_id INTEGER PRIMARY KEY REFERENCES players(id) ON DELETE CASCADE,
            graph_color TEXT NOT NULL DEFAULT 'Blue'
        )''')
        # Fill the four normal players only when they are missing.
        for name in DEFAULT_PLAYERS:
            self.db.execute('INSERT OR IGNORE INTO players(name) VALUES(?)', (name,))
        self.db.commit()

    def build_ui(self):
        # v2.0 visual shell: retain original page widgets and database behavior.
        self.palette = {'cream':'#F7F4EA','forest':'#365D49','sage':'#E7EEE3',
                        'ink':'#28372F','muted':'#647568','white':'#FFFFFF',
                        'line':'#D6E0D1','gold':'#C49A4D'}
        p=self.palette
        self.root.configure(bg=p['cream'])
        self.root.geometry('1320x830')
        self.root.minsize(1080,690)
        style=ttk.Style()
        if 'clam' in style.theme_names(): style.theme_use('clam')
        style.configure('.',font=('Segoe UI',10),background=p['cream'],foreground=p['ink'])
        style.configure('TFrame',background=p['cream'])
        style.configure('TLabel',background=p['cream'],foreground=p['ink'])
        style.configure('TLabelframe',background=p['cream'],bordercolor=p['line'],relief='solid')
        style.configure('TLabelframe.Label',background=p['cream'],foreground=p['forest'],font=('Segoe UI',10,'bold'))
        style.configure('TButton',padding=(12,8),background=p['sage'],foreground=p['forest'],bordercolor=p['line'],font=('Segoe UI',9,'bold'))
        style.map('TButton',background=[('active','#CFDDCA')])
        style.configure('Accent.TButton',background=p['forest'],foreground='white',padding=(14,9))
        style.map('Accent.TButton',background=[('active','#264735')])
        style.configure('TEntry',padding=5,fieldbackground='white')
        style.configure('TCombobox',padding=5,fieldbackground='white')
        style.configure('Treeview',rowheight=31,background='white',fieldbackground='white',foreground=p['ink'],bordercolor=p['line'])
        style.configure('Treeview.Heading',background=p['sage'],foreground=p['forest'],font=('Segoe UI',9,'bold'),padding=7)
        style.configure('Header.TLabel',font=('Segoe UI',16,'bold'),foreground=p['forest'])
        style.configure('Sidebar.TNotebook',background=p['cream'],borderwidth=0)
        # Hide notebook tab strips but retain the original tab-selection API.
        try: style.layout('Sidebar.TNotebook.Tab',[])
        except tk.TclError: pass
        shell=tk.Frame(self.root,bg=p['cream'])
        shell.pack(fill='both',expand=True)
        sidebar=tk.Frame(shell,bg=p['sage'],width=218)
        sidebar.pack(side='left',fill='y')
        sidebar.pack_propagate(False)
        tk.Label(sidebar,text='❧  THE AVIARY',bg=p['sage'],fg=p['forest'],font=('Segoe UI',16,'bold'),anchor='w').pack(fill='x',padx=20,pady=(30,3))
        tk.Label(sidebar,text='WINGSPAN SCORE TRACKER',bg=p['sage'],fg=p['muted'],font=('Segoe UI',8,'bold'),anchor='w').pack(fill='x',padx=21,pady=(0,30))
        content=tk.Frame(shell,bg=p['cream'])
        content.pack(side='left',fill='both',expand=True)
        banner=tk.Frame(content,bg=p['cream'])
        banner.pack(fill='x',padx=24,pady=(19,10))
        self.section_title=tk.Label(banner,text='Overview',bg=p['cream'],fg=p['forest'],font=('Segoe UI',21,'bold'))
        self.section_title.pack(side='left')
        tk.Label(banner,text='❦',font=('Segoe UI',24),bg=p['cream'],fg=p['gold']).pack(side='right')
        self.tabs=ttk.Notebook(content,style='Sidebar.TNotebook')
        self.tabs.pack(fill='both',expand=True,padx=20,pady=(0,16))
        self.dashboard_tab=ttk.Frame(self.tabs,padding=14)
        self.entry_tab=ttk.Frame(self.tabs,padding=8)
        self.entry_notebook=ttk.Notebook(self.entry_tab)
        self.entry_notebook.pack(fill='both',expand=True)
        self.game_tab=ttk.Frame(self.entry_notebook,padding=8)
        self.photo_tab=ttk.Frame(self.entry_notebook,padding=8)
        self.entry_notebook.add(self.game_tab,text='Manual Entry')
        self.entry_notebook.add(self.photo_tab,text='Import Scorebook Photos')
        self.stats_tab=ttk.Frame(self.tabs,padding=12)
        self.game_stats_tab=ttk.Frame(self.tabs,padding=12)
        self.history_tab=ttk.Frame(self.tabs,padding=12)
        self.graph_tab=ttk.Frame(self.tabs,padding=12)
        self.achievements_tab=ttk.Frame(self.tabs,padding=12)
        for tab,text in [(self.dashboard_tab,'Overview'),(self.entry_tab,'Enter Game'),(self.stats_tab,'Players'),
                         (self.game_stats_tab,'Game Statistics'),(self.history_tab,'Game History'),(self.graph_tab,'Graphs'),(self.achievements_tab,'Records')]:
            self.tabs.add(tab,text=text)
        self._nav_buttons=[]
        nav=[('⌂','Overview',self.dashboard_tab,None),('＋','Enter Game',self.entry_tab,self.game_tab),
             ('▧','Photo Import',self.entry_tab,self.photo_tab),('♙','Player Statistics',self.stats_tab,None),
             ('★','Game Statistics',self.game_stats_tab,None),('↗','Graphs',self.graph_tab,None),('✦','Records',self.achievements_tab,None),
             ('☷','Game History',self.history_tab,None)]
        for symbol,title,tab,subtab in nav:
            btn=tk.Button(sidebar,text=f'  {symbol}   {title}',font=('Segoe UI',10),anchor='w',relief='flat',bd=0,
                          bg=p['sage'],fg=p['forest'],activebackground='#CFDDCA',cursor='hand2',
                          padx=12,pady=12,command=lambda t=tab,st=subtab,n=title:self.navigate(t,st,n))
            btn.pack(fill='x',padx=10,pady=2)
            self._nav_buttons.append((title,btn))
        tk.Label(sidebar,text='LOCAL · PRIVATE · OFFLINE',bg=p['sage'],fg=p['muted'],font=('Segoe UI',8)).pack(side='bottom',pady=22)
        self.build_dashboard()
        self.build_game_tab()
        self.build_photo_tab()
        self.build_stats_tab()
        self.build_achievements_tab()
        self.build_game_stats_tab()
        self.build_history_tab()
        self.build_graph_tab()
        self.navigate(self.dashboard_tab,None,'Overview')

    def navigate(self,tab,subtab=None,title=None):
        self.tabs.select(tab)
        if subtab is not None:self.entry_notebook.select(subtab)
        if title:self.section_title.configure(text=title)
        for name,button in self._nav_buttons:
            selected=(name==title)
            button.configure(bg='#CFDDCA' if selected else self.palette['sage'],
                             font=('Segoe UI',10,'bold' if selected else 'normal'))
        if tab==self.dashboard_tab:self.refresh_dashboard()

    def build_dashboard(self):
        p=self.palette
        ttk.Label(self.dashboard_tab,text='Your game night, by the numbers.',font=('Segoe UI',11),foreground=p['muted']).pack(anchor='w',pady=(0,17))
        grid=tk.Frame(self.dashboard_tab,bg=p['cream'])
        grid.pack(fill='x')
        self.dashboard_values={}
        cards=[('Approved Games','games'),('Record Score','high'),('Avg. Winning Score','winner'),('Games for Review','review')]
        for i,(title,key) in enumerate(cards):
            frame=tk.Frame(grid,bg='white',highlightbackground=p['line'],highlightthickness=1)
            frame.grid(row=0,column=i,sticky='nsew',padx=(0 if i==0 else 6,6 if i<3 else 0),ipadx=13,ipady=11)
            grid.grid_columnconfigure(i,weight=1)
            tk.Label(frame,text=title,fg=p['muted'],bg='white',font=('Segoe UI',10)).pack(anchor='w',padx=10,pady=(6,5))
            val=tk.Label(frame,text='—',fg=p['forest'],bg='white',font=('Segoe UI',26,'bold'))
            val.pack(anchor='w',padx=10,pady=(0,4))
            self.dashboard_values[key]=val
        area=ttk.Frame(self.dashboard_tab)
        area.pack(fill='both',expand=True,pady=(24,0))
        leaders=ttk.LabelFrame(area,text='Leaderboard · Total wins',padding=16)
        leaders.pack(side='left',fill='both',expand=True,padx=(0,10))
        self.leaderboard=ttk.Treeview(leaders,columns=('name','wins','played','rate'),show='headings',height=9)
        for col,label,w in [('name','Player',145),('wins','Wins',75),('played','Games',85),('rate','Win rate',95)]:
            self.leaderboard.heading(col,text=label);self.leaderboard.column(col,width=w,anchor='w')
        self.leaderboard.pack(fill='both',expand=True)
        self.leaderboard.bind('<Double-1>',self.open_leaderboard_profile)
        right=ttk.Frame(area)
        right.pack(side='left',fill='both',expand=True,padx=(10,0))
        quick=ttk.LabelFrame(right,text='Quick actions',padding=18)
        quick.pack(fill='x')
        ttk.Button(quick,text='＋  Enter a game',style='Accent.TButton',command=lambda:self.navigate(self.entry_tab,self.game_tab,'Enter Game')).pack(fill='x',pady=6)
        ttk.Button(quick,text='▧  Import scorebook photos',command=lambda:self.navigate(self.entry_tab,self.photo_tab,'Photo Import')).pack(fill='x',pady=6)
        ttk.Button(quick,text='↗  View wins graph',command=lambda:self.navigate(self.graph_tab,None,'Wins Graph')).pack(fill='x',pady=6)
        info=ttk.LabelFrame(right,text='About your records',padding=18)
        info.pack(fill='x',pady=(14,0))
        ttk.Label(info,text='Only approved games contribute to your statistics.\nPhoto drafts stay separate until you verify and approve them.\nUndated historical games remain undated.',wraplength=385,justify='left').pack(anchor='w')

    def open_leaderboard_profile(self,event=None):
        selected=self.leaderboard.selection()
        if not selected:return
        name=self.leaderboard.item(selected[0],'values')[0]
        self.profile_name.set(name)
        self.navigate(self.stats_tab,None,'Player Statistics')
        self.refresh_profile()

    def refresh_dashboard(self):
        if not hasattr(self,'dashboard_values'):return
        rows=list(self.db.execute('SELECT id FROM games'))
        self.dashboard_values['games'].configure(text=str(len(rows)))
        highest=self.db.execute('SELECT MAX(total) FROM scores').fetchone()[0]
        self.dashboard_values['high'].configure(text=str(highest) if highest is not None else '—')
        games=self._game_records()
        avg=sum(g['top'] for g in games)/len(games) if games else None
        self.dashboard_values['winner'].configure(text=f'{avg:.1f}' if avg is not None else '—')
        try:
            pending=self.db.execute("SELECT COUNT(*) FROM photo_drafts WHERE status IN ('review','flagged')").fetchone()[0]
        except sqlite3.Error:
            pending=0
        self.dashboard_values['review'].configure(text=str(pending))
        self.leaderboard.delete(*self.leaderboard.get_children())
        ranked=sorted(self.stats_data(),key=lambda r:(-r[2],r[0].casefold()))
        for row in ranked:
            name,played,wins=row[0],row[1],row[2]
            self.leaderboard.insert('','end',values=(name,wins,played,f'{wins/played*100:.1f}%' if played else '—'))

    def build_game_tab(self):
        manage = ttk.LabelFrame(self.game_tab, text='Players', padding=10)
        manage.pack(fill='x')
        self.new_player = tk.StringVar()
        ttk.Entry(manage,textvariable=self.new_player,width=25).pack(side='left',padx=(0,7))
        ttk.Button(manage,text='Add Player',command=self.add_player).pack(side='left')
        ttk.Label(manage,text='Create players before recording games.',foreground='#667085').pack(side='left',padx=15)
        details = ttk.Frame(self.game_tab)
        details.pack(fill='x',pady=(14,9))
        ttk.Label(details,text='Game date (optional, YYYY-MM-DD):').pack(side='left')
        self.game_date = tk.StringVar(value=date.today().isoformat())
        ttk.Entry(details,textvariable=self.game_date,width=15).pack(side='left',padx=(8,20))
        ttk.Label(details,text='Notes:').pack(side='left')
        self.game_notes = tk.StringVar()
        ttk.Entry(details,textvariable=self.game_notes,width=42).pack(side='left',padx=8,fill='x',expand=True)
        self.game_header = ttk.Label(self.game_tab,text='New game',font=('Segoe UI',11,'bold'))
        self.game_header.pack(anchor='w',pady=5)
        ttk.Label(self.game_tab,text='Final Total calculates automatically from entered categories (empty cells count as 0). If all categories are empty, you can enter an old game total manually.',foreground='#667085',wraplength=1050).pack(anchor='w',pady=(0,8))
        holder=ttk.Frame(self.game_tab)
        holder.pack(fill='both',expand=True)
        canvas=tk.Canvas(holder,highlightthickness=0)
        sc=ttk.Scrollbar(holder,orient='vertical',command=canvas.yview)
        self.score_grid=ttk.Frame(canvas)
        window=canvas.create_window((0,0),window=self.score_grid,anchor='nw')
        self.score_grid.bind('<Configure>',lambda e:canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.bind('<Configure>',lambda e:canvas.itemconfigure(window,width=e.width))
        canvas.configure(yscrollcommand=sc.set)
        canvas.pack(side='left',fill='both',expand=True)
        sc.pack(side='right',fill='y')
        actions=ttk.Frame(self.game_tab)
        actions.pack(fill='x',pady=10)
        ttk.Button(actions,text='Add Another Player Row',command=self.add_score_row).pack(side='left')
        ttk.Button(actions,text='Clear / New Game',command=self.clear_game).pack(side='right',padx=5)
        ttk.Button(actions,text='Save Game',command=self.save_game).pack(side='right',padx=5)
        self.render_score_header()
        self.create_default_rows()

    def render_score_header(self):
        labels=['Player']+[name for _,name in CATEGORIES]+['Final Total','']
        for i,label in enumerate(labels):
            ttk.Label(self.score_grid,text=label,font=('Segoe UI',9,'bold')).grid(row=0,column=i,padx=3,pady=7,sticky='w')

    def create_default_rows(self):
        for name in DEFAULT_PLAYERS:
            self.add_score_row(self.name_to_id(name))

    def add_score_row(self, player_id=None, values=None, total=''):
        idx=len(self.rows)+1
        row={'player':tk.StringVar(),'values':{},'total':tk.StringVar(value=str(total) if total!='' else ''),'manual_total':str(total) if total!='' else ''}
        row['box']=ttk.Combobox(self.score_grid,textvariable=row['player'],width=16,state='readonly')
        row['box'].grid(row=idx,column=0,padx=3,pady=5,sticky='ew')
        if player_id is not None: row['player'].set(self.player_lookup.get(player_id,''))
        for ci,(key,_) in enumerate(CATEGORIES,1):
            val = '' if values is None or values.get(key) is None else str(values[key])
            var=tk.StringVar(value=val)
            row['values'][key]=var
            ent=ttk.Entry(self.score_grid,textvariable=var,width=10)
            ent.grid(row=idx,column=ci,padx=3,pady=5)
            ent.bind('<Return>', self.focus_next_entry)
            var.trace_add('write', lambda *args, r=row: self.update_auto_total(r))
        row['total_entry']=ttk.Entry(self.score_grid,textvariable=row['total'],width=11)
        row['total_entry'].grid(row=idx,column=8,padx=3,pady=5)
        row['total_entry'].bind('<Return>', self.focus_next_entry)
        ttk.Button(self.score_grid,text='Remove',command=lambda r=row:self.remove_score_row(r)).grid(row=idx,column=9,padx=3)
        self.rows.append(row)
        self.update_player_boxes()
        self.update_auto_total(row)

    @staticmethod
    def focus_next_entry(event):
        event.widget.tk_focusNext().focus_set()
        return 'break'

    def update_auto_total(self, row):
        raw=[v.get().strip() for v in row['values'].values()]
        if any(raw):
            # Leave an invalid entry visibly flagged instead of silently computing the wrong number.
            if any(v and not v.isdecimal() for v in raw):
                row['total'].set('')
            else:
                row['total'].set(str(sum(int(v) for v in raw if v)))
            row['total_entry'].configure(state='readonly')
        else:
            row['total_entry'].configure(state='normal')
            row['total'].set(row['manual_total'])

    def remove_score_row(self,row):
        if row not in self.rows:return
        self.rows.remove(row)
        for child in self.score_grid.winfo_children():
            if int(child.grid_info().get('row',-1))>0: child.destroy()
        old=self.rows[:]
        self.rows=[]
        for r in old:
            player_id=self.name_to_id(r['player'].get())
            self.add_score_row(player_id,{k:(v.get() if v.get() else None) for k,v in r['values'].items()},r['total'].get())

    def update_player_boxes(self):
        names=list(self.player_lookup.values())
        for row in self.rows: row['box']['values']=names

    def name_to_id(self,name):
        for pid,pname in self.player_lookup.items():
            if pname==name:return pid
        return None

    def add_player(self):
        name=self.new_player.get().strip()
        if not name: return
        try:
            self.db.execute('INSERT INTO players(name) VALUES(?)',(name,))
            self.db.commit()
            self.new_player.set('')
            self.refresh_all()
        except sqlite3.IntegrityError:
            messagebox.showerror('Duplicate name','A player with this name already exists.')

    @staticmethod
    def parse_number(s,label):
        s=s.strip()
        if not s:return None
        if not s.isdecimal():raise ValueError(f'{label} must be a non-negative whole number.')
        return int(s)

    def save_game(self):
        day=self.game_date.get().strip() or None
        if day:
            try:
                if date.fromisoformat(day).isoformat()!=day:raise ValueError()
            except ValueError:
                messagebox.showerror('Invalid date','Use YYYY-MM-DD or leave the date blank.')
                return
        entries=[]
        seen=set()
        try:
            for row in self.rows:
                name=row['player'].get()
                raw=[v.get().strip() for v in row['values'].values()]
                total_raw=row['total'].get().strip()
                if not name and not any(raw) and not total_raw:continue
                pid=self.name_to_id(name)
                if pid is None:raise ValueError('Select a player for every filled row.')
                if pid in seen:raise ValueError(f'{name} appears twice in this game.')
                seen.add(pid)
                vals={key:self.parse_number(row['values'][key].get(),f'{name}: {label}') for key,label in CATEGORIES}
                total=self.parse_number(total_raw,f'{name}: Final Total')
                any_categories=any(v is not None for v in vals.values())
                if total is None:
                    if not any_categories:raise ValueError(f'Enter a score for {name}.')
                    total=sum(v or 0 for v in vals.values())
                if any_categories and total!=sum(v or 0 for v in vals.values()):
                    raise ValueError(f'{name}: category sum ({sum(v or 0 for v in vals.values())}) differs from final total ({total}).')
                entries.append((pid,vals,total))
            if len(entries)<2:raise ValueError('A game must include at least two players.')
        except ValueError as exc:
            messagebox.showerror('Check scores',str(exc));return
        with self.db:
            if self.editing_game is None:
                cursor=self.db.execute('INSERT INTO games(played_on,notes) VALUES(?,?)',(day,self.game_notes.get().strip()))
                gid=cursor.lastrowid
            else:
                gid=self.editing_game
                self.db.execute('UPDATE games SET played_on=?, notes=? WHERE id=?',(day,self.game_notes.get().strip(),gid))
                self.db.execute('DELETE FROM scores WHERE game_id=?',(gid,))
            for pid,vals,total in entries:
                self.db.execute('''INSERT INTO scores(game_id,player_id,birds,bonus_cards,end_round_goals,eggs,cached_food,tucked_cards,nectar,total)
                    VALUES (?,?,?,?,?,?,?,?,?,?)''',(gid,pid,*[vals[k] for k,_ in CATEGORIES],total))
        messagebox.showinfo('Saved',f'Game #{gid} saved with {len(entries)} players.')
        self.clear_game()
        self.refresh_all()

    def clear_game(self):
        self.editing_game=None
        self.game_header.config(text='New game')
        self.game_date.set(date.today().isoformat())
        self.game_notes.set('')
        for w in self.score_grid.winfo_children():
            if int(w.grid_info().get('row',-1))>0:w.destroy()
        self.rows=[]
        self.create_default_rows()

    def build_stats_tab(self):
        profile=ttk.LabelFrame(self.stats_tab,text='Player Profile  •  Explore your game-night performance',padding=12)
        profile.pack(fill='x',pady=(0,12))
        bar=ttk.Frame(profile);bar.pack(fill='x',pady=(0,9))
        ttk.Label(bar,text='Choose player:',font=('Segoe UI',10,'bold')).pack(side='left',padx=(0,10))
        self.profile_name=tk.StringVar()
        self.profile_choice=ttk.Combobox(bar,textvariable=self.profile_name,state='readonly',width=22)
        self.profile_choice.pack(side='left')
        self.profile_choice.bind('<<ComboboxSelected>>',lambda e:self.refresh_profile())
        self.profile_color_swatch=tk.Label(bar,text='   ●   ',font=('Segoe UI',16,'bold'),bg=self.palette['cream'])
        self.profile_color_swatch.pack(side='left',padx=12)
        self.profile_cards=ttk.Frame(profile);self.profile_cards.pack(fill='x')
        self.profile_card_labels={}
        for index,(key,title) in enumerate([('played','Games'),('wins','Wins'),('rate','Win Rate'),('average','Average Score'),('record','High / Low'),('recent','Last 10'),('streak','Longest Streak'),('podium','2nd / 3rd / 4th')]):
            card=ttk.Frame(self.profile_cards,padding=9,relief='solid',borderwidth=1)
            card.grid(row=index//4,column=index%4,sticky='nsew',padx=3,pady=3)
            self.profile_cards.columnconfigure(index%4,weight=1)
            ttk.Label(card,text=title,foreground=self.palette['muted']).pack(anchor='w')
            v=ttk.Label(card,text='—',font=('Segoe UI',16,'bold'),foreground=self.palette['forest'])
            v.pack(anchor='w');self.profile_card_labels[key]=v
        graphrow=ttk.Frame(profile);graphrow.pack(fill='x',pady=(12,0))
        ttk.Label(graphrow,text='Average scoring categories',font=('Segoe UI',10,'bold')).pack(anchor='w')
        self.profile_chart_area=ttk.Frame(profile);self.profile_chart_area.pack(fill='x')
        ttk.Separator(self.stats_tab).pack(fill='x',pady=(0,8))
        ttk.Label(self.stats_tab,text='Player leaderboard',style='Header.TLabel').pack(anchor='w',pady=(0,6))
        ttk.Label(self.stats_tab,text='A tied first-place score counts as one win for each tied player. Category averages exclude unrecorded (blank) categories.',foreground='#667085',wraplength=1000).pack(anchor='w',pady=(0,10))
        cols=['Player','Games','Wins','Win %','High Score','Low Score','Average Total','Last 10 W-L','Longest Win Streak','2nd','3rd','4th']+[f'Avg {x}' for _,x in CATEGORIES]
        frame=ttk.Frame(self.stats_tab);frame.pack(fill='both',expand=True)
        self.stats_tree=ttk.Treeview(frame,columns=cols,show='headings')
        for col in cols:
            self.stats_tree.heading(col,text=col)
            self.stats_tree.column(col,width=105 if col not in ('Player','Average Total') else 130,anchor='center')
        sx=ttk.Scrollbar(frame,orient='horizontal',command=self.stats_tree.xview)
        sy=ttk.Scrollbar(frame,orient='vertical',command=self.stats_tree.yview)
        self.stats_tree.configure(xscrollcommand=sx.set,yscrollcommand=sy.set)
        self.stats_tree.pack(side='top',fill='both',expand=True)
        sx.pack(fill='x')
        ttk.Button(self.stats_tab,text='Export Statistics to CSV',command=self.export_stats).pack(anchor='e',pady=8)

    def refresh_profile(self):
        names=[name for _,name in self.db.execute('SELECT id,name FROM players ORDER BY name COLLATE NOCASE')]
        self.profile_choice['values']=names
        selected=self.profile_name.get()
        if selected not in names:
            selected=names[0] if names else ''
            self.profile_name.set(selected)
        if not selected:return
        item=self.db.execute('SELECT id FROM players WHERE name=? COLLATE NOCASE',(selected,)).fetchone()
        if not item:return
        pid=item[0]
        rows=list(self.db.execute('SELECT s.total,'+','.join('s.'+k for k,_ in CATEGORIES[:6])+' FROM scores s WHERE s.player_id=?',(pid,)))
        totals=[r[0] for r in rows]
        extra=self._player_extra_stats().get(pid,{})
        results=extra.get('results',[]); recent=results[-10:]
        n=len(rows);wins=sum(results)
        values={'played':str(n),'wins':str(wins),'rate':f'{100*wins/n:.1f}%' if n else '—',
                'average':f'{sum(totals)/n:.1f}' if n else '—',
                'record':f'{max(totals)} / {min(totals)}' if n else '—',
                'recent':f'{sum(recent)}–{len(recent)-sum(recent)}' if recent else '—',
                'streak':str(extra.get('max_streak',0)),
                'podium':' / '.join(str(x) for x in extra.get('places',[0,0,0]))}
        for k,v in values.items():self.profile_card_labels[k].configure(text=v)
        color=self.db.execute('SELECT graph_color FROM player_preferences WHERE player_id=?',(pid,)).fetchone()
        chosen=color[0] if color else DEFAULT_COLORS.get(selected.casefold(),'Blue')
        self.profile_color_swatch.configure(fg=COLOR_OPTIONS.get(chosen,'#365D49'))
        for child in self.profile_chart_area.winfo_children():child.destroy()
        if Figure is None or not rows:return
        labels=[label for _,label in CATEGORIES[:6]]
        averages=[sum((r[i+1] for r in rows if r[i+1] is not None),0)/max(1,sum(r[i+1] is not None for r in rows)) for i in range(6)]
        fig=Figure(figsize=(10,2.2),dpi=90,facecolor='#F7F4EA')
        ax=fig.add_subplot(111);ax.barh(labels[::-1],averages[::-1],color=COLOR_OPTIONS.get(chosen,'#365D49'))
        ax.set_xlabel('Average points (only games with recorded category)',fontsize=9)
        ax.spines[['top','right']].set_visible(False)
        fig.tight_layout()
        canvas=FigureCanvasTkAgg(fig,master=self.profile_chart_area);canvas.draw()
        canvas.get_tk_widget().pack(fill='x');self.profile_chart_canvas=canvas

    def build_achievements_tab(self):
        ttk.Label(self.achievements_tab,text='The Aviary Records',style='Header.TLabel').pack(anchor='w',pady=(0,8))
        ttk.Label(self.achievements_tab,text='Milestones calculated from approved games only. Blank categories are excluded from category records.',foreground=self.palette['muted'],wraplength=950).pack(anchor='w',pady=(0,12))
        frame=ttk.Frame(self.achievements_tab);frame.pack(fill='both',expand=True)
        self.records_tree=ttk.Treeview(frame,columns=('award','holder','value','details'),show='headings')
        for key,heading,width in [('award','Record / Achievement',280),('holder','Player(s)',180),('value','Value',120),('details','Description',420)]:
            self.records_tree.heading(key,text=heading);self.records_tree.column(key,width=width,anchor='w')
        scrollbar=ttk.Scrollbar(frame,orient='vertical',command=self.records_tree.yview)
        self.records_tree.configure(yscrollcommand=scrollbar.set)
        self.records_tree.pack(side='left',fill='both',expand=True);scrollbar.pack(side='right',fill='y')
        ttk.Button(self.achievements_tab,text='Refresh Records',command=self.refresh_achievements).pack(anchor='e',pady=9)

    def refresh_achievements(self):
        self.records_tree.delete(*self.records_tree.get_children())
        games=self._game_records()
        if not games:return
        entries=[]
        for game in games:
            sorted_scores=sorted(game['scores'],key=lambda x:-x[2])
            if len(sorted_scores)>=2:
                entries.append((sorted_scores[0][2]-sorted_scores[1][2],game['id'],sorted_scores[0][1]))
        def add(label,holders,value,description):self.records_tree.insert('','end',values=(label,holders,value,description))
        rows=list(self.db.execute('SELECT p.name,s.total,s.birds,s.bonus_cards,s.end_round_goals,s.eggs,s.cached_food,s.tucked_cards FROM scores s JOIN players p ON p.id=s.player_id'))
        for label,ix,desc in [('High Flyer',1,'Highest individual score'),('Bird Collector',2,'Most bird points in one game'),('Bonus Hunter',3,'Most bonus card points'),('Goal Getter',4,'Most end-of-round goal points'),('Egg Expert',5,'Most egg points in a game'),('Food Stasher',6,'Most food cached on cards'),('Tuck Master',7,'Most tucked cards')]:
            eligible=[r for r in rows if r[ix] is not None]
            if eligible:
                value=max(r[ix] for r in eligible)
                add(label,', '.join(sorted({r[0] for r in eligible if r[ix]==value})),value,desc)
        if entries:
            biggest=max(x[0] for x in entries);smallest=min(x[0] for x in entries)
            add('Biggest Victory',', '.join(sorted({n for v,g,n in entries if v==biggest})),biggest,'Largest difference between first and second')
            add('Nail-Biter',', '.join(sorted({n for v,g,n in entries if v==smallest})),smallest,'Smallest difference between first and second')
        extras=self._player_extra_stats()
        names=dict(self.db.execute('SELECT id,name FROM players'))
        if extras:
            high=max(x['max_streak'] for x in extras.values())
            add('Hot Streak',', '.join(names[i] for i,e in extras.items() if e['max_streak']==high),high,'Longest consecutive winning streak')
        counts=Counter(n for n,total,*cats in rows if total>=100)
        if counts:
            high=max(counts.values());add('Century Club',', '.join(n for n,c in counts.items() if c==high),high,'Games scoring at least 100 points')

    def stats_data(self):
        select=', '.join(f'AVG(s.{key})' for key,_ in CATEGORIES)
        q=f'''SELECT p.name,COUNT(s.game_id),
          COALESCE(SUM(CASE WHEN s.total=(SELECT MAX(s2.total) FROM scores s2 WHERE s2.game_id=s.game_id) THEN 1 ELSE 0 END),0),
          MAX(s.total),MIN(s.total),AVG(s.total),{select}
          FROM players p LEFT JOIN scores s ON s.player_id=p.id GROUP BY p.id ORDER BY 3 DESC,5 DESC,p.name'''
        return self.db.execute(q).fetchall()

    def _game_records(self):
        """Only official approved games. Chronology: recorded date, then game ID; undated follow dated."""
        result=[]
        games=self.db.execute("SELECT id,played_on FROM games ORDER BY CASE WHEN played_on IS NULL THEN 1 ELSE 0 END,played_on,id").fetchall()
        for gid,day in games:
            rows=self.db.execute("SELECT s.player_id,p.name,s.total FROM scores s JOIN players p ON p.id=s.player_id WHERE s.game_id=?",(gid,)).fetchall()
            if rows:
                result.append({'id':gid,'date':day,'scores':rows,'top':max(r[2] for r in rows), 'bottom':min(r[2] for r in rows)})
        return result

    def _player_extra_stats(self):
        players={pid:{'results':[],'streak':0,'max_streak':0,'places':[0,0,0]} for pid, in self.db.execute('SELECT id FROM players')}
        for game in self._game_records():
            for pid,name,total in game['scores']:
                record=players[pid]
                won=(total==game['top'])
                record['results'].append(won)
                record['streak']=record['streak']+1 if won else 0
                record['max_streak']=max(record['max_streak'],record['streak'])
                # Standard competition ranking: ties share a place, e.g. 1,1,3,4.
                place=1+sum(other[2]>total for other in game['scores'])
                if 2<=place<=4:record['places'][place-2]+=1
        return players

    def refresh_stats(self):
        self.stats_tree.delete(*self.stats_tree.get_children())
        extras=self._player_extra_stats()
        ids={name.casefold():pid for pid,name in self.db.execute('SELECT id,name FROM players')}
        for name,games,wins,best,lowest,avg,*cats in self.stats_data():
            fmt=lambda n: '—' if n is None else f'{n:.1f}'
            e=extras[ids[name.casefold()]]
            recent=e['results'][-10:]
            last_ten=f'{sum(recent)}-{len(recent)-sum(recent)}' if recent else '—'
            self.stats_tree.insert('', 'end',values=[name,games,wins,f'{wins/games*100:.1f}%' if games else '—',best if best is not None else '—',lowest if lowest is not None else '—',fmt(avg),last_ten,e['max_streak'],*e['places'],*[fmt(v) for v in cats]])

    def build_game_stats_tab(self):
        ttk.Label(self.game_stats_tab,text='Game Statistics',style='Header.TLabel').pack(anchor='w',pady=(0,6))
        ttk.Label(self.game_stats_tab,text='Calculated from approved games only. Tied winners all count as winners; losing scores exclude tied winners. Dates are used when known; undated games follow by entry ID.',foreground='#667085',wraplength=1000).pack(anchor='w',pady=(0,10))
        panel=ttk.Frame(self.game_stats_tab)
        panel.pack(fill='both',expand=True)
        self.game_stats_tree=ttk.Treeview(panel,columns=('metric','value','detail'),show='headings',height=18)
        for col,label,width in [('metric','Statistic',320),('value','Value',130),('detail','Game / Additional detail',550)]:
            self.game_stats_tree.heading(col,text=label)
            self.game_stats_tree.column(col,width=width,anchor='w')
        self.game_stats_tree.pack(fill='both',expand=True)
        ttk.Button(self.game_stats_tab,text='Refresh Game Statistics',command=self.refresh_game_stats).pack(anchor='e',pady=8)

    def refresh_game_stats(self):
        tree=self.game_stats_tree
        tree.delete(*tree.get_children())
        games=self._game_records()
        if not games:
            tree.insert('','end',values=('Games recorded','0','No approved games yet'))
            return
        winning=[];losing=[];totals=[];margin=[];spread=[];tie_count=0
        for game in games:
            rows=game['scores'];gid=game['id'];top=game['top']
            winner_names=', '.join(name for _,name,total in rows if total==top)
            winning.append((top,gid,winner_names))
            losing.extend((total,gid,name) for _,name,total in rows if total<top)
            totals.append((sum(t for _,_,t in rows),gid,len(rows)))
            spread.append((top-game['bottom'],gid))
            if sum(t==top for _,_,t in rows)>1:tie_count+=1
            if len(rows)>1:
                scores=sorted((t for _,_,t in rows),reverse=True)
                margin.append((scores[0]-scores[1],gid))
        def add(title,value,detail=''):
            tree.insert('','end',values=(title,value,detail))
        def avg(vals):return f'{sum(vals)/len(vals):.2f}' if vals else '—'
        def describe(entry):return f'Game #{entry[1]}'
        add('Approved games',len(games))
        add('Player score entries',sum(len(g['scores']) for g in games))
        add('Average winning score',avg([x[0] for x in winning]))
        add('Average losing score',avg([x[0] for x in losing]),'Excludes winners, including tied winners')
        add('Highest combined game points',max(totals)[0],describe(max(totals)))
        add('Lowest combined game points',min(totals)[0],describe(min(totals)))
        add('Average combined game points',avg([x[0] for x in totals]))
        add('Highest winning score',max(winning)[0],f'{describe(max(winning))} — {max(winning)[2]}')
        add('Lowest winning score',min(winning)[0],f'{describe(min(winning))} — {min(winning)[2]}')
        if losing:
            add('Highest non-winning score',max(losing)[0],f'{describe(max(losing))} — {max(losing)[2]}')
            add('Lowest non-winning score',min(losing)[0],f'{describe(min(losing))} — {min(losing)[2]}')
        add('Average winning margin',avg([x[0] for x in margin]),'First vs second highest (ties yield zero)')
        add('Biggest winning margin',max(margin)[0],describe(max(margin)))
        add('Biggest score spread',max(spread)[0],describe(max(spread)))
        add('Games tied for first',tie_count)

    def export_stats(self):
        dest=filedialog.asksaveasfilename(defaultextension='.csv',filetypes=[('CSV','*.csv')],initialfile='wingspan_stats.csv')
        if not dest:return
        with open(dest,'w',newline='',encoding='utf-8-sig') as f:
            writer=csv.writer(f)
            writer.writerow([self.stats_tree.heading(c)['text'] for c in self.stats_tree['columns']])
            for item in self.stats_tree.get_children():writer.writerow(self.stats_tree.item(item,'values'))
        messagebox.showinfo('Exported',f'Exported to {dest}')

    def build_history_tab(self):
        ttk.Label(self.history_tab,text='Recorded games',style='Header.TLabel').pack(anchor='w',pady=(0,10))
        columns=('ID','Date','Winner(s)','Players / Scores','Notes')
        self.history_tree=ttk.Treeview(self.history_tab,columns=columns,show='headings',selectmode='browse')
        for c in columns:
            self.history_tree.heading(c,text=c)
            self.history_tree.column(c,width={'ID':50,'Date':105,'Winner(s)':155,'Players / Scores':380,'Notes':260}[c],anchor='w')
        self.history_tree.pack(fill='both',expand=True)
        actions=ttk.Frame(self.history_tab);actions.pack(fill='x',pady=10)
        ttk.Button(actions,text='Edit Selected Game',command=self.edit_selected).pack(side='left',padx=4)
        ttk.Button(actions,text='Delete Selected Game',command=self.delete_selected).pack(side='left',padx=4)
        ttk.Button(actions,text='Export Game Scores to CSV',command=self.export_history).pack(side='right',padx=4)
        self.history_tree.bind('<Double-1>',lambda event:self.edit_selected())

    def refresh_history(self):
        self.history_tree.delete(*self.history_tree.get_children())
        games=self.db.execute('SELECT id,played_on,notes FROM games ORDER BY CASE WHEN played_on IS NULL THEN 1 ELSE 0 END,played_on DESC,id DESC').fetchall()
        for gid,day,notes in games:
            scores=self.db.execute('SELECT p.name,s.total FROM scores s JOIN players p ON p.id=s.player_id WHERE s.game_id=? ORDER BY s.total DESC,p.name',(gid,)).fetchall()
            if not scores:continue
            best=scores[0][1]
            winners=', '.join(name for name,total in scores if total==best)
            desc=' • '.join(f'{name}: {total}' for name,total in scores)
            self.history_tree.insert('','end',iid=str(gid),values=(gid,day or '(undated)',winners,desc,notes))

    def selected_id(self):
        selection=self.history_tree.selection()
        if not selection:
            messagebox.showwarning('Select a game','Select a game from history first.')
            return None
        return int(selection[0])

    def edit_selected(self):
        gid=self.selected_id()
        if gid is None:return
        day,notes=self.db.execute('SELECT played_on,notes FROM games WHERE id=?',(gid,)).fetchone()
        scores=self.db.execute('SELECT player_id,birds,bonus_cards,end_round_goals,eggs,cached_food,tucked_cards,nectar,total FROM scores WHERE game_id=? ORDER BY player_id',(gid,)).fetchall()
        self.clear_game()
        for w in self.score_grid.winfo_children():
            if int(w.grid_info().get('row',-1))>0:w.destroy()
        self.rows=[]
        for data in scores:self.add_score_row(data[0],dict(zip([key for key,_ in CATEGORIES],data[1:-1])),data[-1])
        self.editing_game=gid
        self.game_header.config(text=f'Editing game #{gid}')
        self.game_date.set(day or '')
        self.game_notes.set(notes)
        self.tabs.select(self.entry_tab)
        self.entry_notebook.select(self.game_tab)

    def delete_selected(self):
        gid=self.selected_id()
        if gid is None:return
        if not messagebox.askyesno('Delete game',f'Permanently delete game #{gid}?'):return
        with self.db:self.db.execute('DELETE FROM games WHERE id=?',(gid,))
        if self.editing_game==gid:self.clear_game()
        self.refresh_all()

    def export_history(self):
        dest=filedialog.asksaveasfilename(defaultextension='.csv',filetypes=[('CSV','*.csv')],initialfile='wingspan_games.csv')
        if not dest:return
        with open(dest,'w',newline='',encoding='utf-8-sig') as f:
            wr=csv.writer(f)
            wr.writerow(['Game ID','Date','Player',*[name for _,name in CATEGORIES],'Total','Notes'])
            for row in self.db.execute('''SELECT g.id,g.played_on,p.name,s.birds,s.bonus_cards,s.end_round_goals,s.eggs,s.cached_food,s.tucked_cards,s.nectar,s.total,g.notes FROM games g JOIN scores s ON s.game_id=g.id JOIN players p ON p.id=s.player_id ORDER BY COALESCE(g.played_on,'9999'),g.id,p.name'''):
                wr.writerow(row)
        messagebox.showinfo('Exported',f'Exported to {dest}')

    def build_graph_tab(self):
        ttk.Label(self.graph_tab,text='Performance Graphs',style='Header.TLabel').pack(anchor='w',pady=(0,6))
        ttk.Label(self.graph_tab,text='Games are ordered by date (then entry ID). Undated games are shown after dated games, in entry order. Each tied winner earns one win.',foreground='#667085',wraplength=1000).pack(anchor='w',pady=(0,8))
        selector=ttk.Frame(self.graph_tab);selector.pack(fill='x',pady=(0,9))
        ttk.Label(selector,text='Metric:').pack(side='left',padx=(0,7))
        self.graph_metric=tk.StringVar(value='Cumulative wins')
        metrics=ttk.Combobox(selector,textvariable=self.graph_metric,state='readonly',width=28,values=['Cumulative wins','Score by game','Rolling average score (last 5)','Cumulative win percentage'])
        metrics.pack(side='left')
        metrics.bind('<<ComboboxSelected>>',lambda event:self.refresh_graph())
        self.colors_frame=ttk.LabelFrame(self.graph_tab,text='Player line colors (saved automatically)',padding=8)
        self.colors_frame.pack(fill='x',pady=(0,8))
        self.color_controls=ttk.Frame(self.colors_frame)
        self.color_controls.pack(fill='x')
        self.color_vars={}
        self.chart_area=ttk.Frame(self.graph_tab)
        self.chart_area.pack(fill='both',expand=True)
        ttk.Button(self.graph_tab,text='Refresh Graph',command=self.refresh_graph).pack(anchor='e',pady=8)

    def refresh_color_controls(self):
        for widget in self.color_controls.winfo_children(): widget.destroy()
        self.color_vars={}
        names=self.db.execute('SELECT id,name FROM players ORDER BY name COLLATE NOCASE').fetchall()
        prefs=dict(self.db.execute('SELECT player_id,graph_color FROM player_preferences'))
        for i,(pid,name) in enumerate(names):
            choice=prefs.get(pid,DEFAULT_COLORS.get(name.casefold(),'Blue'))
            if choice not in COLOR_OPTIONS: choice='Blue'
            v=tk.StringVar(value=choice)
            self.color_vars[pid]=v
            frame=ttk.Frame(self.color_controls)
            frame.grid(row=i//4,column=i%4,sticky='w',padx=(0,17),pady=3)
            ttk.Label(frame,text=name+':',width=11).pack(side='left')
            picker=ttk.Combobox(frame,textvariable=v,values=list(COLOR_OPTIONS),state='readonly',width=10)
            picker.pack(side='left')
            picker.bind('<<ComboboxSelected>>',lambda event,player_id=pid,var=v:self.save_graph_color(player_id,var.get()))

    def save_graph_color(self, pid, color):
        if color not in COLOR_OPTIONS: return
        with self.db:
            self.db.execute('INSERT INTO player_preferences(player_id,graph_color) VALUES(?,?) ON CONFLICT(player_id) DO UPDATE SET graph_color=excluded.graph_color',(pid,color))
        self.refresh_graph()

    def refresh_graph(self):
        for child in self.chart_area.winfo_children():child.destroy()
        if Figure is None:
            ttk.Label(self.chart_area,text='Graphing requires Matplotlib. Install it using: python -m pip install matplotlib',font=('Segoe UI',11)).pack(pady=30)
            return
        players=list(self.db.execute('SELECT id,name FROM players ORDER BY name'))
        games=list(self.db.execute('SELECT id,played_on FROM games ORDER BY CASE WHEN played_on IS NULL THEN 1 ELSE 0 END,played_on,id'))
        if not games or not players:
            ttk.Label(self.chart_area,text='Add at least one saved game to see the graph.').pack(pady=30)
            return
        graph=Figure(figsize=(10,5),dpi=100)
        ax=graph.add_subplot(111)
        metric=self.graph_metric.get()
        wins={pid:0 for pid,_ in players}
        appearances={pid:0 for pid,_ in players}
        score_history={pid:[] for pid,_ in players}
        track={pid:[0 if metric in ('Cumulative wins','Cumulative win percentage') else float('nan')] for pid,_ in players}
        for gid,_ in games:
            results=self.db.execute('SELECT player_id,total FROM scores WHERE game_id=?',(gid,)).fetchall()
            if results:
                top=max(total for _,total in results)
                for pid,total in results:
                    if pid not in track:continue
                    appearances[pid]+=1;score_history[pid].append(total)
                    if total==top:wins[pid]+=1
            for pid in track:
                if metric=='Cumulative wins':v=wins[pid]
                elif metric=='Cumulative win percentage':v=100*wins[pid]/appearances[pid] if appearances[pid] else float('nan')
                elif metric=='Score by game':v=next((total for player,total in results if player==pid),float('nan'))
                else:v=sum(score_history[pid][-5:])/min(len(score_history[pid]),5) if score_history[pid] else float('nan')
                track[pid].append(v)
        xs=list(range(len(games)+1))
        color_prefs=dict(self.db.execute('SELECT player_id,graph_color FROM player_preferences'))
        for pid,name in players:
            if any(v for v in track[pid]) or any(self.db.execute('SELECT 1 FROM scores WHERE player_id=? LIMIT 1',(pid,)).fetchone() for _ in [0]):
                chosen=color_prefs.get(pid,DEFAULT_COLORS.get(name.casefold(),'Blue'))
                (ax.step(xs,track[pid],where='post',label=name,linewidth=3.0,color=COLOR_OPTIONS.get(chosen,COLOR_OPTIONS['Blue'])) if metric=='Cumulative wins' else ax.plot(xs,track[pid],label=name,linewidth=2.6,color=COLOR_OPTIONS.get(chosen,COLOR_OPTIONS['Blue'])))
        ax.set(xlabel='Game sequence (dated games first; undated by entry order)',ylabel=metric,xlim=(0,max(1,len(games))))
        if metric=='Cumulative wins':ax.set_ylim(bottom=0)
        if metric=='Cumulative win percentage':ax.set_ylim(0,100)
        ax.set_xticks(range(0,len(games)+1,max(1,len(games)//15)))
        if metric=='Cumulative wins':ax.yaxis.get_major_locator().set_params(integer=True)
        ax.grid(alpha=.25)
        ax.legend(loc='upper left')
        graph.tight_layout()
        canvas=FigureCanvasTkAgg(graph,master=self.chart_area)
        canvas.draw()
        canvas.get_tk_widget().pack(fill='both',expand=True)
        self.chart_canvas=canvas

    def refresh_all(self):
        self.player_lookup=dict(self.db.execute('SELECT id,name FROM players ORDER BY name'))
        self.update_player_boxes()
        self.refresh_stats()
        self.refresh_profile()
        self.refresh_achievements()
        self.refresh_game_stats()
        self.refresh_history()
        self.refresh_color_controls()
        self.refresh_graph()
        self.refresh_dashboard()

    def close(self):
        self.db.close()
        self.root.destroy()

if __name__=='__main__':
    window=tk.Tk()
    App(window)
    window.mainloop()
