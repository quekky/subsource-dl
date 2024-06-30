import io
import re
import os
import itertools
import unicodedata
import zipfile
from collections import defaultdict
from guessit import guessit
import requests
import argparse
from thefuzz import process
from pprint import pprint


wanted_language = "English"
guessit_options = {
    'date_year_first': True,
    'episode_prefer_number': True
}

subsource_api_url = "https://api.subsource.net/api/"
subsource_header = {'Content-Type': 'application/json'}


#: Subtitle extensions
SUBTITLE_EXTENSIONS = ('.srt', '.sub', '.smi', '.ssa', '.ass', '.mpl')
#: Video extensions
VIDEO_EXTENSIONS = ('.3g2', '.3gp', '.3gp2', '.3gpp', '.60d', '.ajp', '.asf', '.asx', '.avchd', '.avi', '.bik',
                    '.bix', '.box', '.cam', '.dat', '.divx', '.dmf', '.dv', '.dvr-ms', '.evo', '.flc', '.fli',
                    '.flic', '.flv', '.flx', '.gvi', '.gvp', '.h264', '.m1v', '.m2p', '.m2ts', '.m2v', '.m4e',
                    '.m4v', '.mjp', '.mjpeg', '.mjpg', '.mkv', '.moov', '.mov', '.movhd', '.movie', '.movx', '.mp4',
                    '.mpe', '.mpeg', '.mpg', '.mpv', '.mpv2', '.mxf', '.nsv', '.nut', '.ogg', '.ogm' '.ogv', '.omf',
                    '.ps', '.qt', '.ram', '.rm', '.rmvb', '.swf', '.ts', '.vfw', '.vid', '.video', '.viv', '.vivo',
                    '.vob', '.vro', '.wm', '.wmv', '.wmx', '.wrap', '.wvx', '.wx', '.x264', '.xvid')



def is_meta_match(x, y):
    if args.matchtype == 'single-session-episode':
        return 'E'+str(x['episode']) in y['filename']

    else: # args.matchtype=='auto'
        if x['type'] == 'movie' and y['type'] == 'movie':
            return True

        if x['type'] == 'episode' and y['type'] == 'episode':
            if x['season'] == y['season']:
                if 'episode' not in x or 'episode' not in y:
                    return 'date' in x and 'date' in y and x['date'] == y['date']
                if isinstance(x['episode'], list):
                    i=y['episode']
                    return bool(set(x['episode']).intersection(i if isinstance(i, list) else [i]))
                if isinstance(y['episode'], list):
                    return x['episode'] in y['episode']
                return x['episode'] == y['episode']
            #if season not match, still try to match by date
            return 'date' in x and 'date' in y and x['date'] == y['date']


def cleanchar(text):
    text = unicodedata.normalize('NFKD', text)
    text = re.sub(u'[\u2013\u2014\u3161\u1173\uFFDA]', '-', text)
    text = re.sub(u'[\u00B7\u2000-\u206F\u22C5\u318D]', '.', text)
    return text


def search_subsource(title, season):
    r = requests.post(f"{subsource_api_url}searchMovie", headers=subsource_header, json={'query': title}).json()
    # match title
    titles = {i: r['found'][i]['title'] for i in range(len(r['found']))}
    score = process.extractOne(title, titles)
    if score[1] > 90:
        found0 = r['found'][score[2]]
        # match season
        seasons = [str(i['number']) for i in found0['seasons']]
        score = process.extractOne(str(season), seasons)
        if score[1] > 95:
            seasonfound = score[0]
        elif '1' in seasons:
            seasonfound = '1'
        elif '0' in seasons:
            seasonfound = '0'
        else:
            raise Exception("Unable for find season on subsource")

        return {'movieName': found0['linkName'], 'season': 'season-'+seasonfound}


def get_subs(movieName, season):
    r = requests.post(f"{subsource_api_url}getMovie", headers=subsource_header, json={'movieName': movieName, 'season': season}).json()
    for subtitle in r['subs']:
        if subtitle['lang'] == wanted_language:
            title = cleanchar(subtitle['releaseName'])
            if args.matchtype=='single-session-episode':
                subtitle_meta={}
            else: # args.matchtype=='auto'
                subtitle_meta = guessit(title, guessit_options)
                subtitle_meta.setdefault('season', 1)
                subtitle_meta['session_pack'] = subtitle_meta['type'] == 'episode' and (
                    'episode' not in subtitle_meta or isinstance(subtitle_meta['episode'], list)
                )
            subtitle_meta['filename'] = title
            subtitle_meta['subtitle_object'] = subtitle
            yield subtitle_meta


def get_downloadlink(subtitle_object):
    r = requests.post(f"{subsource_api_url}getSub", headers=subsource_header, json={'movie': subtitle_object['linkName'], 'lang': subtitle_object['lang'], 'id':subtitle_object['subId']}).json()
    return r['sub']['downloadToken']


def download_single_sub(video_filename, subtitle_object):
    try:
        downloadToken = get_downloadlink(subtitle_object)
        r = requests.get(f"{subsource_api_url}downloadSub/{downloadToken}", headers=subsource_header)
        html = r.content
        try:
            with zipfile.ZipFile(io.BytesIO(html)) as z:
                # print("Found sub: "+subtitle_object['releaseName'])
                vid_name = os.path.splitext(video_filename)[0]
                if args.savepath:
                    vid_name = os.path.join(args.savepath, os.path.split(vid_name)[1])
                for infofile in z.infolist():
                    sub_ext = os.path.splitext(infofile.filename)[1]
                    if sub_ext in SUBTITLE_EXTENSIONS:
                        file = open(vid_name + sub_ext, 'wb')
                        file.write(z.read(infofile))
                        print("File downloaded: ", vid_name + sub_ext)
        except:
            print("Error on file {1}, status {2} ".format((subtitle_object['releaseName'],r.status_code)))
    except:
        print("Cannot find download link for "+subtitle_object['releaseName'])


def download_sesson_pack(v_metas, subtitle_object):
    try:
        downloadToken = get_downloadlink(subtitle_object)
        r = requests.get(f"{subsource_api_url}downloadSub/{downloadToken}", headers=subsource_header)
        html = r.content
        try:
            with zipfile.ZipFile(io.BytesIO(html)) as z:
                # print("Found season pack sub: "+subtitle_object['releaseName'])
                for infofile in z.infolist():
                    sub_ext = os.path.splitext(infofile.filename)[1]
                    if sub_ext in SUBTITLE_EXTENSIONS:
                        zip_meta = guessit(cleanchar(infofile.filename), guessit_options)
                        zip_meta.setdefault('season', 1)
                        zip_meta['session_pack'] = False
                        # print("Inside zip:"+infofile.filename)
                        for v_meta in filter(lambda v: is_meta_match(v, zip_meta), v_metas):
                            vid_name = os.path.splitext(v_meta['filename'])[0]
                            if args.savepath:
                                vid_name = os.path.join(args.savepath, os.path.split(vid_name)[1])
                            file = open(vid_name + sub_ext, 'wb')
                            file.write(z.read(infofile))
                            print("File downloaded: ", vid_name + sub_ext)
                            v_meta['downloaded'] = True
        except:
            print("Error on file {1}, status {2} ".format((subtitle_object['releaseName'],r.status_code)))
    except:
        print("Cannot find download link for "+subtitle_object['releaseName'])


def download_subtitles(files):
    video_metas = defaultdict(defaultdict)
    for f in files:
        video_meta = guessit(cleanchar(f), guessit_options)
        video_meta['filename'] = f
        video_meta['downloaded'] = False
        video_meta.setdefault('season', 1)
        title = unicodedata.normalize('NFKD', video_meta['title'])
        if not title in video_metas:
            video_metas[title] = defaultdict(list)
        video_metas[title][video_meta['season']].append(video_meta)

    for title, _ in video_metas.items():
        for season, v_metas in _.items():
            params = search_subsource(title, season)
            subtitle_metas = list(get_subs(**params))

            if args.matchtype=='auto' and len(v_metas) >= 5:
                #season packs have priority
                for subtitle_meta in filter(lambda s: s['session_pack'], subtitle_metas):
                    #if pack does not have the ep we want, skip it
                    if 'episode' in subtitle_meta and isinstance(subtitle_meta['episode'], list):
                        eps = [v['episode'] for v in v_metas if not v['downloaded']]
                        eps = set(itertools.chain.from_iterable([i if isinstance(i, list) else [i] for i in eps]))
                        if not eps.intersection(subtitle_meta['episode']):
                            continue
                    # print("trying to download:"+subtitle_meta['filename'])
                    download_sesson_pack(v_metas, subtitle_meta['subtitle_object'])
                    #if all subs downloaded
                    if all([v['downloaded'] for v in v_metas]):
                        break

            #download others files that season pack doesnt get
            for video_meta in filter(lambda v: not v['downloaded'], v_metas):
                for subtitle_meta in filter(lambda s: is_meta_match(video_meta, s), subtitle_metas):
                    download_single_sub(video_meta['filename'], subtitle_meta['subtitle_object'])
                    break


def find_video_files(path):
    if os.path.isdir(path):
        allfiles=[]
        for root, dirs, files in os.walk(path):
            allfiles.extend([os.path.join(root ,f) for f in files])
        videos = list(filter(lambda f: f.endswith(VIDEO_EXTENSIONS), allfiles))
        for v in videos:
            if not any(p.startswith(os.path.splitext(v)[0]) and p.endswith(SUBTITLE_EXTENSIONS) for p in allfiles):
                yield v

    elif path.endswith(VIDEO_EXTENSIONS):
        dirpath, filename = os.path.split(path)
        dirpath = dirpath or '.'
        fileroot, fileext = os.path.splitext(filename)

        for p in os.listdir(dirpath):
            if p.startswith(fileroot) and p.endswith(SUBTITLE_EXTENSIONS):
                return
        yield path


'''
subsource-dl.py filename.mkv [where_to_save]
    will download sub for filename.mkv

subsource-dl.py /somedir
    will download subs for all video files in /somedir

note: subsource-dl.py will skip any file that already have subs
'''
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('path')
    parser.add_argument('--savepath', help='where to save')
    parser.add_argument('--matchtype', default='auto', help='how to match', choices={'auto' ,'single-session-episode'})
    args = parser.parse_args()

    videos = find_video_files(os.path.normpath(args.path))
    download_subtitles(videos)
