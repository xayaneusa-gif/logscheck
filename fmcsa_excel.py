"""Read-only FMCSA PDF-to-XLSX table parser; embedded images never executed or discarded.
No OCR: image-only content is explicitly reported as not machine-readable.
"""
from __future__ import annotations
import io, re, zipfile, posixpath
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timedelta, date
from validate_single_driver_locations import transition_rule, location_match, valid_coords

NS={'x':'http://schemas.openxmlformats.org/spreadsheetml/2006/main', 'r':'http://schemas.openxmlformats.org/officeDocument/2006/relationships'}
CELL=re.compile(r'([A-Z]+)(\d+)$')
STATUS={'OFF':'OFF_DUTY','SB':'SLEEPER_BERTH','DR':'DRIVING','ON':'ON_DUTY'}
MAX_BYTES=35_000_000

def colnum(ref):
    n=0
    for c in CELL.match(ref).group(1): n=n*26+ord(c)-64
    return n

def serial_date(value, date1904=False):
    try:
        n=float(value)
        if not 20000<n<100000: return None
        return (datetime(1904,1,1) if date1904 else datetime(1899,12,30))+timedelta(days=n)
    except (ValueError,TypeError,OverflowError): return None

def fraction_time(value):
    try:
        n=float(value)
        if not 0<=n<1: return None
        return timedelta(seconds=round(n*86400))
    except (ValueError,TypeError): return None

def clean(v): return str(v or '').strip()

def parse_xlsx(source):
    raw=source.read() if hasattr(source,'read') else source
    if len(raw)>MAX_BYTES: raise ValueError('Workbook exceeds the 35 MB safety limit.')
    try: z=zipfile.ZipFile(io.BytesIO(raw))
    except (zipfile.BadZipFile,TypeError) as exc: raise ValueError('Not a valid XLSX workbook.') from exc
    with z:
        names=z.namelist()
        if any(i.startswith('/') or '..' in i.split('/') for i in names): raise ValueError('Unsafe XLSX archive paths.')
        if sum(i.file_size for i in z.infolist())>160_000_000: raise ValueError('Expanded XLSX exceeds safety limit.')
        if 'xl/workbook.xml' not in names: raise ValueError('Missing workbook metadata.')
        book=ET.fromstring(z.read('xl/workbook.xml'))
        date1904=(book.find('x:workbookPr',NS) is not None and book.find('x:workbookPr',NS).get('date1904') in ('1','true'))
        strings=[]
        if 'xl/sharedStrings.xml' in names:
            root=ET.fromstring(z.read('xl/sharedStrings.xml'))
            strings=[''.join((t.text or '') for t in si.findall('.//x:t',NS)) for si in root.findall('x:si',NS)]
        sheets=[n for n in names if re.fullmatch(r'xl/worksheets/sheet\d+\.xml',n)]
        if not sheets: raise ValueError('No readable worksheets.')
        all_days=[]; warnings=[]; images=len([n for n in names if n.startswith('xl/media/')]); rowcount=0
        for sheet_name in sorted(sheets):
            root=ET.fromstring(z.read(sheet_name))
            rows={}
            for row in root.findall('.//x:sheetData/x:row',NS):
                rid=int(row.get('r')); cells={}
                for cell in row.findall('x:c',NS):
                    ref=cell.get('r',''); m=CELL.match(ref)
                    if not m: continue
                    v=cell.find('x:v',NS)
                    if cell.get('t')=='inlineStr':
                        value=''.join((t.text or '') for t in cell.findall('.//x:is/x:t',NS))
                    elif v is None: value=''
                    elif cell.get('t')=='s':
                        try:value=strings[int(v.text)]
                        except (ValueError,IndexError,TypeError):value=''
                    else:value=v.text or ''
                    if value!='':cells[colnum(ref)]=value
                rows[rid]=cells
            rowcount+=len(rows)
            starts=[r for r,c in rows.items() if clean(c.get(1)).casefold()=='record date' and clean(c.get(9)).casefold().startswith('usdot')]
            for idx,header in enumerate(starts):
                boundary=starts[idx+1] if idx+1<len(starts) else max(rows)+1
                get=lambda r,c: clean(rows.get(r,{}).get(c))
                record_date=serial_date(get(header+1,1),date1904)
                if record_date is None:
                    warnings.append(f'{sheet_name} row {header}: invalid daily record date');continue
                day=record_date.date()
                meta={'Date':day.isoformat(),'Driver':get(header+3,9),'Driver ID':get(header+5,9),
                      'Co-driver':get(header+3,19),'Co-driver ID':get(header+5,19),
                      'USDOT':get(header+1,9),'License number':get(header+1,19),
                      'License state':get(header+1,33),'ELD ID':get(header+1,44),
                      'Trailer ID':get(header+1,58),'Timezone':get(header+3,1),
                      'ELD manufacturer':get(header+3,33),'Shipping ID':get(header+3,44),
                      'Data diagnostic indicator':get(header+3,58),'Truck tractor ID':get(header+5,33),
                      'Unidentified driver records':get(header+5,44),'Malfunction indicator':get(header+5,58),
                      'Carrier':get(header+7,1),'Start/end odometer':get(header+7,19),
                      'VIN':get(header+7,33),'Exempt driver status':get(header+7,44),
                      'Start/end engine hours':get(header+7,58),'Source sheet':sheet_name}
                section=None; events=[]; diagnostics=[]; other=[]; annotations=[]; last_time=None; current_date=day
                for r in range(header+8,boundary):
                    cells=rows.get(r,{})
                    label=clean(cells.get(1)); low=label.casefold()
                    if 'changes in driver' in low and 'duty status' in low: section='events';last_time=None;current_date=day;continue
                    if 'login/logout' in low and 'diagnostic' in low: section='diagnostics';last_time=None;current_date=day;continue
                    if 'change in driver' in low and ('cycle' in low or 'operating zone' in low): section='other';last_time=None;current_date=day;continue
                    if low.startswith('comments, remarks'):section='annotations';continue
                    if low.startswith('additional hours'):section='additional';continue
                    if low.startswith('cmv engine power'):section='power';last_time=None;current_date=day;continue
                    if low.startswith('unidentified driver profile'):section='unidentified';continue
                    if low.startswith('i certify'):section='certification';continue
                    if section not in ('events','diagnostics','other','power','annotations','unidentified'):continue
                    explicit=serial_date(label,date1904)
                    if explicit:
                        current_date=explicit.date();last_time=None;continue
                    dt=fraction_time(label)
                    if section in ('events','diagnostics','other','power') and dt is not None:
                        if last_time is not None and dt>last_time+timedelta(seconds=3):current_date-=timedelta(days=1)
                        last_time=dt
                        time_value=(datetime.combine(current_date,datetime.min.time())+dt)
                        event=clean(cells.get(3))
                        if not event:continue
                        location=clean(cells.get(7))
                        lat=clean(cells.get(19 if section=='events' else 16))
                        lon=''
                        if section=='events':
                            for c in (24,23,25,22,21,20):
                                candidate=clean(cells.get(c))
                                try:
                                    v=float(candidate)
                                    if -180<=v<=180 and abs(v)>60:lon=candidate;break
                                except ValueError:pass
                        record={'Date':time_value.date().isoformat(),'Time':time_value.isoformat(timespec='seconds'),
                                'Event':event,'Location':location,'Latitude':lat,'Longitude':lon,
                                'Sequence ID':clean(cells.get(67)),'Source row':r,'Source sheet':sheet_name,
                                'Driver':meta['Driver'],'Section':section,
                                'Record status':clean(cells.get(58)),'Record origin':clean(cells.get(64)),
                                'Truck':clean(cells.get(40))}
                        if section=='events':events.append(record)
                        elif section=='diagnostics':
                            record['Additional info']=clean(cells.get(11)) or clean(cells.get(7));diagnostics.append(record)
                        else:other.append(record)
                    elif section=='annotations' and (label or clean(cells.get(12))):
                        if label.casefold().startswith('date '):continue
                        annotations.append({'Date':day.isoformat(),'Source row':r,'Comment':clean(cells.get(12)),
                                            'Sequence ID':clean(cells.get(7))})
                if not events: warnings.append(f'{sheet_name} {day}: no machine-readable duty-status events')
                all_days.append({'metadata':meta,'events':events,'diagnostics':diagnostics,'other':other,'annotations':annotations})
        if not all_days:raise ValueError('No recognizable FMCSA daily-log sections found. Check workbook layout.')
        return {'days':all_days,'warnings':warnings,'embedded_images':images,'sheets':len(sheets),'rows_scanned':rowcount}

def validate(parsed, gps_matching=False, exclude_zip=True, tolerance_meters=100, review_radius=500):
    events=[]; issues=list(parsed['warnings'])
    for day in parsed['days']:
        for e in day['events']:
            if e['Event'].upper() in STATUS:events.append(e)
            elif e['Event'].upper() not in ('INT',):issues.append(f"Unrecognized event {e['Event']!r} at row {e['Source row']}")
    # Duplicate rows can arise when PDF export repeats a boundary event on adjacent days.
    seen=set();unique=[]
    for e in sorted(events,key=lambda x:(x['Time'],x['Source sheet'],x['Source row'])):
        key=(e['Driver'],e['Time'],e['Event'],e['Sequence ID'] or e['Location'])
        if key not in seen:unique.append(e);seen.add(key)
    results=[]
    for a,b in zip(unique,unique[1:]):
        if a['Driver']!=b['Driver']:
            continue
        rule=transition_rule(STATUS[a['Event'].upper()],STATUS[b['Event'].upper()])
        if not rule:continue
        aa={'reverse_geo_data':a['Location'],'latitude':a['Latitude'],'longitude':a['Longitude']}
        bb={'reverse_geo_data':b['Location'],'latitude':b['Latitude'],'longitude':b['Longitude']}
        result,dist,method=location_match(aa,bb,tolerance_meters,gps_matching,exclude_zip,review_radius)
        # PDF-formatted offset descriptions (e.g. '1.8mi E City CA') are approximate.
        # Differing descriptions alone do not establish a physical location violation.
        if result=='LOCATION_MISMATCH' and not gps_matching and (re.match(r'^\d+(?:\.\d+)?\s*mi\b',a['Location'],re.I) or re.match(r'^\d+(?:\.\d+)?\s*mi\b',b['Location'],re.I)):
            result='REVIEW_APPROXIMATE_LOCATION'
        results.append({'Result':result,'Date':b['Date'],'Driver':b['Driver'],'Rule':rule,
                        'From time':a['Time'],'From status':a['Event'],'From location':a['Location'],
                        'To time':b['Time'],'To status':b['Event'],'To location':b['Location'],
                        'From row':a['Source row'],'To row':b['Source row'],
                        'From sequence':a['Sequence ID'],'To sequence':b['Sequence ID'],
                        'GPS distance (m)':round(dist,1) if dist is not None else None,'Method':method})
    return {'results':results,'counts':dict(Counter(r['Result'] for r in results)),
            'events':len(unique),'warnings':issues}
