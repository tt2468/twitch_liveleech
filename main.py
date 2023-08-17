# twitch_liveleech - Copyright 2022 IRLToolkit Inc.

# Usage: twitch_liveleech.py [channel] [output path]

import sys
channelName = sys.argv[1]
outputPath = sys.argv[2]

import logging
logging.basicConfig(level=logging.DEBUG, handlers=[logging.FileHandler('twitch_ll_{}.log'.format(channelName)), logging.StreamHandler()], format="%(asctime)s [%(levelname)s] %(message)s")

import os
import signal
import string
import time
import datetime
import threading
import subprocess
import requests
import shortuuid
import streamlink
import ffmpeg

CHECK_SLEEP_DURATION = 60 # Seconds
VOD_SEGMENT_DURATION = 3600 * 6 # 6 Hours
FMP4_FRAGMENT_DURATION = 4 # Seconds
REMUX_AFTER_DOWNLOAD = True
REMUX_REMOVE_FRAGMENTED_FILE = True
TEMP_FILE_DIRECTORY = '/tmp' # Segment list file stored here, segments themselves use outputPath
TEMP_FILE_PREFIX = 'vod_downloader_{}_'.format(channelName)

twitchClientId = os.getenv('TWITCH_LIVELEECH_CLIENT_ID')
twitchClientSecret = os.getenv('TWITCH_LIVELEECH_CLIENT_SECRET')
twitchApiHeader = os.getenv('TWITCH_LIVELEECH_API_HEADER') or ''

months = ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec']
exit = False # This should be mutexed. TODO I guess.
ffmpegProc = None
runFileWatcher = False
fragmentWatcherStopped = threading.Event()

def append_file(fileName, data):
    with open(fileName, 'a') as f:
        f.write('\n======================================================================\n')
        f.write(data)

def make_tmp_filename(extension: str = 'txt'):
    fileName = '{}{}.{}'.format(TEMP_FILE_PREFIX, shortuuid.uuid(), extension)
    return os.path.join(TEMP_FILE_DIRECTORY, fileName)

def get_channel_title():
    req = requests.post('https://id.twitch.tv/oauth2/token?client_id={}&client_secret={}&grant_type=client_credentials'.format(twitchClientId, twitchClientSecret))
    if req.status_code != requests.codes.ok:
        logging.warning('Failed to get Twitch app auth token due to HTTP error. Code: {} | Text: {}'.format(req.status_code, req.text))
        return 'UNKNOWN TITLE'
    twitchAuthorization = req.json()['access_token']
    headers = {'Client-Id': twitchClientId, 'Authorization': 'Bearer ' + twitchAuthorization}
    req = requests.get('https://api.twitch.tv/helix/users?login={}'.format(channelName.lower()), headers=headers)
    if req.status_code != requests.codes.ok:
        logging.warning('Failed to get Twitch user id due to HTTP error. Code: {} | Text: {}'.format(req.status_code, req.text))
        return 'UNKNOWN TITLE'
    channelId = req.json()['data'][0]['id']
    req = requests.get('https://api.twitch.tv/helix/channels?broadcaster_id={}'.format(channelId), headers=headers)
    if req.status_code != requests.codes.ok:
        logging.warning('Failed to get channel title due to HTTP error. Code: {} | Text: {}'.format(req.status_code, req.text))
        return 'UNKNOWN TITLE'
    data = req.json()
    return data['data'][0]['title']

def check_generate_dir(title):
    date = datetime.date.today()
    dir = '{}/{}_{}'.format(outputPath, months[date.month - 1], date.year)
    if not os.path.exists(dir):
        logging.info('Creating directory: {}'.format(dir))
        os.makedirs(dir)
    path = '{}/{}_{}_{}_%03d.mp4'.format(dir, date.day, title, int(time.time()))
    return dir + '/', path

def launch_fragment_watcher(segmentFile):
    global runFileWatcher
    runFileWatcher = True
    fragmentWatcherStopped.clear()
    def run(segmentFile):
        logging.info('Segment watcher thread started.')
        try:
            while runFileWatcher:
                waitUntil = time.time() + 240
                while time.time() < waitUntil:
                    if not runFileWatcher:
                        break
                    time.sleep(0.5)
                # No runFileWatcher check because we want one final pass
                remuxFiles = []
                try:
                    with open(segmentFile, 'r') as f:
                        for line in f:
                            line = line.rstrip()
                            if not os.path.exists(line):
                                continue
                            remuxFiles.append(line)
                except FileNotFoundError:
                    continue # The file may not always exist in theory
                except:
                    logging.exception('Unhandled fragment watcher file read exception:\n')
                    continue
                for file in remuxFiles:
                    logging.info('Found new segment to remux: {}'.format(file))
                    finalPath = file.replace('.fragmented.mp4', '.mp4') # Somewhat hacky, shouldn't be an issue tho
                    stream = ffmpeg.input(file).output(finalPath, c = 'copy') # Simple remux
                    cmd = ffmpeg.compile(stream, 'ffmpeg', overwrite_output = True)
                    ffmpegProc = subprocess.Popen(cmd, stdin = subprocess.DEVNULL, stdout = subprocess.DEVNULL, stderr = subprocess.DEVNULL)
                    code = ffmpegProc.wait()
                    if code:
                        logging.error('FFmpeg remux on segment file `{}` failed. Return code: {}'.format(file, code))
                        continue
                    else:
                        logging.info('Finished muxing segment source file: {}'.format(file))
                    if REMUX_REMOVE_FRAGMENTED_FILE:
                        try:
                            os.remove(file)
                            logging.debug('File deleted: {}'.format(file))
                        except:
                            logging.exception('Failed to delete file `{}`:\n'.format(file))
        except:
            logging.exception('')
        finally:
            fragmentWatcherStopped.set()
            logging.info('Segment watcher thread finished.')
    thr = threading.Thread(target = run, args = [segmentFile])
    thr.start()

def stop_fragment_watcher():
    global runFileWatcher
    runFileWatcher = False
    logging.debug('Stopping fragment watcher thread (if running)...')
    if not fragmentWatcherStopped.is_set():
        if not fragmentWatcherStopped.wait(15):
            logging.error('Timed out waiting for fragment watcher stop!')

def signal_handler(sig, frame):
    logging.info('\nCTRL-C captured - exiting...')
    global exit
    alreadyExiting = exit
    exit = True
    if ffmpegProc:
        if alreadyExiting:
            ffmpegProc.send_signal(signal.SIGINT)
        else:
            ffmpegProc.stdin.write(b'q')

def main():
    global exit
    global ffmpegProc

    session = streamlink.session.Streamlink()
    options = streamlink.options.Options()
    options.set('disable-hosting', True)
    options.set('disable-ads', True)
    options.set('disable-reruns', True)
    if twitchApiHeader:
        options.set('api-header', {'Authorization': twitchApiHeader})
    _, pluginClass, resolvedUrl = session.resolve_url('https://twitch.tv/{}'.format(channelName))
    plugin = pluginClass(session, resolvedUrl, options)

    signal.signal(signal.SIGINT, signal_handler)

    waitUntil = 0
    while not exit:
        logging.debug('Sleeping for {} seconds...'.format(CHECK_SLEEP_DURATION))
        while time.time() < waitUntil: # Interruptable sleep, non-async python has no cond wait_until
            if exit:
                break
            time.sleep(0.5)
        waitUntil = time.time() + CHECK_SLEEP_DURATION
        logging.debug('Done.')
        if exit:
            break

        try:
            streams = plugin.streams()
        except (streamlink.exceptions.PluginError, requests.exceptions.ConnectionError):
            logging.error('Failed to fetch stream via streamlink.')
            continue
        except:
            logging.exception('Unhandled exception fetching current channel streams:\n')
            continue
        if not streams:
            logging.info('No streams are available.')
            continue
        elif 'best' not in streams:
            logging.error('`best` stream not available!')
            break
        logging.info('Stream found! Opening ffmpeg...')

        title = 'UNKNOWN TITLE'
        try:
            title = get_channel_title()
            logging.debug('Current stream title: {}'.format(title))
        except requests.exceptions.ConnectionError:
            pass
        validChars = "-.() %s%s" % (string.ascii_letters, string.digits)
        title = ''.join(c for c in title if c in validChars)

        dir, path = check_generate_dir(title)

        outputOptions = {
            'vcodec': 'copy',
            'acodec': 'aac',
            'format': 'segment',
            'segment_format': 'mp4',
            'segment_format_options': 'frag_duration={}:movflags=empty_moov+delay_moov'.format(1000000 * FMP4_FRAGMENT_DURATION),
            'segment_time': VOD_SEGMENT_DURATION,
            'reset_timestamps': 1
        }

        if REMUX_AFTER_DOWNLOAD:
            segmentFileName = make_tmp_filename() # This file never gets deleted, but it's in /tmp and is usually small
            outputOptions['segment_list'] = segmentFileName
            outputOptions['segment_list_entry_prefix'] = dir
            outputOptions['segment_list_flags'] = '+live'
            outputOptions['segment_list_type'] = 'flat'
            path = path.replace('%03d', '%03d.fragmented')
            launch_fragment_watcher(segmentFileName)

        logging.info('Writing download to: {}...'.format(path))
        stream = ffmpeg.input(streams['best'].url).output(path, **outputOptions)
        cmd = ffmpeg.compile(stream, 'ffmpeg', overwrite_output = True)
        logFile = open('twitch_ll_download_{}.log'.format(channelName), 'a')
        try:
            ffmpegProc = subprocess.Popen(cmd, stdin = subprocess.PIPE, stdout = subprocess.DEVNULL, stderr = logFile)
            ffmpegProc.wait()
            logging.info('Stream ended!')
            waitUntil = 0 # Don't sleep after a download session in case stream is still live
        except Exception as e:
            logging.exception('Process communicate returned error:\n')
        logFile.close()
        stop_fragment_watcher()
        ffmpegProc = None

if __name__ == '__main__':
    logging.getLogger('urllib3').setLevel(logging.INFO)
    logging.getLogger('streamlink').setLevel(logging.INFO)

    if not twitchClientId or not twitchClientSecret:
        logging.critical('Missing TWITCH_LIVELEECH_CLIENT_ID or TWITCH_LIVELEECH_CLIENT_SECRET env variable(s).')
        os._exit(1)

    main()
