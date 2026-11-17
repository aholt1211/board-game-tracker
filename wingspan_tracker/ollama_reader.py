"""Ollama vision transcription for Wingspan. No database writes."""
import json
import re
from pathlib import Path
from PIL import Image, ImageOps

MODEL = 'qwen3-vl:4b'
PROMPT = '''You are transcribing handwritten Wingspan board game scores from a photograph. Accuracy is more important than completing every field.
PAGE LAYOUT: The page ordinarily contains TWO games, top and bottom, each with four player columns. Read player abbreviations from their headings: L=Liv; AL=Alex; K=Keaton; Aa=Aaron. Column order may change. If heading is illegible, player must be null.
READ ROWS IN ORDER: 1 Birds 2 Bonus cards 3 End-of-round goals 4 Eggs 5 Food on cards 6 Tucked cards 7 Final handwritten TOTAL.
There is NO nectar category in these games. Do not flag missing nectar. Set nectar to null.
IMPORTANT TOTAL-SCORE INSTRUCTIONS: The final total is the number handwritten in the bottom TOTAL row. Read this directly from the image; never calculate, substitute or change it to match the category scores. If unreadable use null. Keep total independent of category points.
HANDWRITING: Use clear replacement numbers rather than crossed-out ones. Never invent unreadable numbers. Use null for uncertain numbers; list these in uncertain_fields. Do not guess a player based on usual order. Do not invent game dates.
Return ONLY a valid JSON object with a games array and each game containing position and players array. Every player has player, birds, bonus, goals, eggs, food, tucked, nectar, total, uncertain_fields. Example shape:
{"games":[{"position":"top","players":[{"player":"Liv","birds":null,"bonus":null,"goals":null,"eggs":null,"food":null,"tucked":null,"nectar":null,"total":null,"uncertain_fields":[]}]}]}
Include every visible game and player. Numbers must be integers or null.'''

FIELD_MAP = {'birds':'birds','bonus':'bonus_cards','goals':'end_round_goals','eggs':'eggs','food':'cached_food','tucked':'tucked_cards'}
ALIASES = {'l':'Liv','liv':'Liv','al':'Alex','alex':'Alex','k':'Keaton','keaton':'Keaton','aa':'Aaron','aaron':'Aaron'}

def normalize_number(value):
    return value if type(value) is int and value >= 0 else None

def reconcile_six_as_zero(values, written_total):
    """Conservative OCR recovery: one erroneous digit 6 -> 0 in one category.

    Return (new_values, correction) only when a unique single digit
    substitution closes the independent handwritten-total discrepancy.
    Never modify the written total, invent a missing field, or guess between
    multiple plausible categories.
    """
    keys=tuple(FIELD_MAP.values())
    if written_total is None or any(values.get(k) is None for k in keys):
        return values, None
    delta=sum(values[k] for k in keys)-written_total
    if delta <= 0:
        return values, None
    candidates=[]
    for key in keys:
        number=values[key]
        digits=str(number)
        for index, digit in enumerate(digits):
            if digit != '6':
                continue
            proposed=int(digits[:index]+'0'+digits[index+1:])
            if number-proposed == delta:
                candidates.append((key,number,proposed))
    if len(candidates)!=1:
        return values, None
    key,old,new=candidates[0]
    updated=values.copy()
    updated[key]=new
    return updated, {'field':key,'from':old,'to':new}


def read_photo(path, model=MODEL):
    try:
        from ollama import chat
    except ImportError as exc:
        raise RuntimeError('Ollama Python integration missing. Run: py -m pip install ollama') from exc
    path=Path(path)
    # Preserve the source; limit long edge only for stability on AMD Vulkan.
    with Image.open(path) as im:
        im=ImageOps.exif_transpose(im).convert('RGB')
        im.thumbnail((1600,1600),Image.Resampling.LANCZOS)
        import io
        buffer=io.BytesIO(); im.save(buffer,format='JPEG',quality=95)
        image_bytes=buffer.getvalue()
    response=chat(model=model,messages=[{'role':'user','content':PROMPT,'images':[image_bytes]}],format='json',think=False,options={'temperature':0,'num_ctx':8192})
    raw=response.message.content or ''
    try:
        parsed=json.loads(raw)
    except (ValueError,TypeError):
        match=re.search(r'\{.*\}',raw,re.S)
        if not match:raise RuntimeError('The AI did not return score data. Try another photo or retry.')
        try:parsed=json.loads(match.group())
        except ValueError as exc:raise RuntimeError('The AI response is not valid JSON. Try again.') from exc
    games=parsed.get('games') if isinstance(parsed,dict) else None
    if not isinstance(games,list) or len(games)!=2:
        raise RuntimeError('Expected two games on this page; AI detected a different number. No drafts created.')
    drafts=[]
    for i,game in enumerate(games,1):
        items=game.get('players',[]) if isinstance(game,dict) else []
        issues=[]; players=[]; seen=set(); corrections=[]
        if len(items)!=4:issues.append(f'Expected 4 players, AI found {len(items)}')
        for idx, item in enumerate(items):
            if not isinstance(item,dict):continue
            raw_name=item.get('player')
            name=ALIASES.get(str(raw_name).strip().casefold()) if raw_name is not None else None
            if not name or name in seen:
                issues.append(f'Player column {idx+1} unidentified or repeated; confirm names')
                # Suggest only a default for review UI; never approve automatically.
                name=next((n for n in ('Liv','Alex','Keaton','Aaron') if n not in seen),'Liv')
            seen.add(name)
            vals={target:normalize_number(item.get(src)) for src,target in FIELD_MAP.items()}
            vals['nectar']=None
            total=normalize_number(item.get('total'))
            uncertain=item.get('uncertain_fields',[])
            uncertain=uncertain if isinstance(uncertain,list) else []
            confidence={k:'suggested' for k in vals}
            confidence['nectar']='not applicable'
            confidence['total']='suggested'
            for k,v in vals.items():
                if k!='nectar' and v is None:issues.append(f'{name}: {k} unreadable')
            if total is None:issues.append(f'{name}: written total unreadable')
            for u in uncertain:
                if isinstance(u,str) and u.lower()!='nectar':issues.append(f'{name}: uncertain {u}')
            correction=None
            # Only use this exception when the AI did not itself mark the value uncertain.
            # A unique 6->0 change must reconcile with the independently read total.
            if not uncertain:
                vals,correction=reconcile_six_as_zero(vals,total)
                if correction:
                    confidence[correction['field']]='auto-corrected (6 to 0); verify'
            if total is not None and all(vals[k] is not None for k in vals if k!='nectar'):
                computed=sum(vals[k] for k in vals if k!='nectar')
                if computed!=total:issues.append(f'{name}: categories add to {computed}, written total reads {total}')
            if correction:
                corrections.append(f"{name} {correction['field']}: {correction['from']} -> {correction['to']} (6 read as 0; VERIFY PHOTO)")
            players.append({'player':name,'values':vals,'total':total,'confidence':confidence,'source':'ollama'})
        if len(players)<2:issues.append('Too few players detected')
        drafts.append({'players':players,'date':'','notes':f'Ollama photo import, game {i}',
                       'name_confirmed':False,'image_path':str(path),'game_on_page':i,'issues':issues,'auto_corrections':corrections})
    return drafts
