#!/usr/bin/env python3
"""Small, bounded public-source URL discovery; no keys are created or purchased."""
import argparse
import csv
import io
import json
import time
import urllib.parse
from pathlib import Path
from campaign_research.collect import Engine, origin
from campaign_research.common import atomic_write, digest, now, write_json


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--committees',required=True)
    parser.add_argument('--out',required=True)
    args=parser.parse_args()
    out=Path(args.out);out.mkdir(parents=True,exist_ok=True)
    engine=Engine(out,64*1024*1024,24)
    receipts=[];findings=[]
    def fetch(url,label,delay=2):
        receipt={'source_url':url,'label':label,'checked_at':now()}
        try:
            engine.allowed(url)
            engine.delays[origin(url)]=max(delay,engine.delays.get(origin(url),2))
            status,headers,body=engine.gated_request(url,max_bytes=16*1024*1024)
            receipt.update(http_status=status,bytes=len(body),sha256=digest(body))
            relative='objects/'+digest(body)+'.body'
            atomic_write(out/relative,body);receipt['path']=relative
            if status!=200:
                receipt['outcome']='access_or_service_error';return None
            receipt['outcome']='fetched';return body
        except Exception as error:
            receipt['outcome']='blocked_or_error';receipt['reason']=str(error) if isinstance(error,ValueError) else type(error).__name__
            return None
        finally:
            receipts.append(receipt);write_json(out/'source-receipts.json',receipts)
    # These are explicit anonymous public download endpoints, not scraped search pages.
    body=fetch('https://election.org/api/v1/downloads/federal-candidates-2026.csv','Public2026candidateCSV')
    if body:
        try:
            reader=csv.DictReader(io.StringIO(body.decode('utf-8-sig')))
            rows=list(reader)
            write_json(out/'public-candidate-csv-profile.json',{'columns':reader.fieldnames,'rows':len(rows)})
        except Exception as error:
            write_json(out/'public-candidate-csv-profile.json',{'error':type(error).__name__})
    body=fetch('https://dataverse.harvard.edu/api/datasets/:persistentId/?persistentId=doi%3A10.7910%2FDVN%2FBZ2JRS','PublishedHistoricalCampaignURLRoster')
    if body:
        try:
            metadata=json.loads(body)
            files=metadata.get('data',{}).get('latestVersion',{}).get('files',[])
            write_json(out/'historical-file-index.json',[{'id':r['dataFile']['id'],'filename':r['dataFile']['filename'],'bytes':r['dataFile'].get('filesize')} for r in files])
            for entry in files:
                file=entry['dataFile']
                if file['filename']=='release_roster.csv':
                    roster=fetch('https://dataverse.harvard.edu/api/access/datafile/'+str(file['id']),'HistoricalURLRosterCSV')
                    if roster:atomic_write(out/'historical-release-roster.csv',roster)
        except Exception as error:
            write_json(out/'historical-profile-error.json',{'error':type(error).__name__})
    records=json.loads(Path(args.committees).read_text())['rows']
    by_id={r['committee_id']:r for r in records}
    ids=sorted(by_id)
    # Six100-record calls cover the linked586committees. DEMO_KEY is the FEC's
    # documented public test key.90second spacing stays within its strict quota.
    for start in range(0,len(ids),100):
        group=ids[start:start+100]
        query=urllib.parse.urlencode([('api_key','DEMO_KEY'),('per_page','100')]+[('committee_id',cid) for cid in group])
        body=fetch('https://api.open.fec.gov/v1/committees/?'+query,'FECCommitteeRegistrationWebsites',delay=90)
        if not body:
            # Do not hammer a denial, throttle, or inaccessible origin.
            break
        try:data=json.loads(body)
        except ValueError:break
        result=data.get('results',[])
        write_json(out/('fec-batch-profile-%03d.json'%start),{'records':len(result),'fields':sorted(result[0]) if result else []})
        for record in result:
            cid=record.get('committee_id')
            if cid in by_id:
                row=by_id[cid]
                findings.append({**row,'reported_website':record.get('website') or '',
                                 'reported_committee_name':record.get('name'),
                                 'reported_candidate_ids':record.get('candidate_ids'),
                                 'website_evidence':'Exact linked committee ID, official FEC registration response',
                                 'current_site_identity':'not yet inspected','response_sha256':digest(body),'retrieved_at':now()})
        write_json(out/'fec-website-findings.json',findings)
    write_json(out/'summary.json',{'finished_at':now(),'source_requests':engine.requests,
                                 'committee_records':len(findings),'reported_websites':sum(bool(r['reported_website']) for r in findings),
                                 'source_outcomes':[{k:r.get(k) for k in ('label','outcome','http_status','reason')} for r in receipts]})
    print(json.dumps({'committee_records':len(findings),'reported_websites':sum(bool(r['reported_website']) for r in findings),'requests':engine.requests}))


if __name__=='__main__':main()
