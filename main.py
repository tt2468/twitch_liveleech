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
import requests
import streamlink
import ffmpeg

CHECK_SLEEP_DURATION = 60 # Seconds
VOD_SEGMENT_DURATION = 3600 * 6 # 6 Hours
FMP4_FRAGMENT_DURATION = 30 # Seconds

twitchClientId = os.getenv('TWITCH_LIVELEECH_CLIENT_ID')
twitchClientSecret = os.getenv('TWITCH_LIVELEECH_CLIENT_SECRET')
twitchApiHeader = os.getenv('TWITCH_LIVELEECH_API_HEADER') or ''

months = ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec']
exit = False # This should be mutexed. TODO I guess.
proc = None

def append_file(fileName, data):
    with open(fileName, 'a') as f:
        f.write('\n======================================================================\n')
        f.write(data)

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
    return path

def signal_handler(sig, frame):
    print('\nCTRL-C captured - exiting...')
    global exit
    exit = True
    if proc:
        proc.send_signal(signal.SIGINT)

def main():
    global exit
    global proc

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

        path = check_generate_dir(title)

        logging.info('Writing download to: {}...'.format(path))
        fragDuration = 1000000 * FMP4_FRAGMENT_DURATION
        stream = ffmpeg.input(streams['best'].url).output(path,
            vcodec = 'copy',
            acodec = 'aac',
            format = 'segment',
            segment_format = 'mp4',
            segment_format_options = 'frag_duration={}:movflags=empty_moov+delay_moov'.format(fragDuration),
            segment_time = VOD_SEGMENT_DURATION,
            reset_timestamps = 1
        )
        proc = ffmpeg.run_async(stream, pipe_stderr = True)
        try:
            _, err = proc.communicate()
            append_file('twitch_ll_download_{}.log'.format(channelName), err.decode())
            logging.info('Stream ended!')
            waitUntil = 0 # Don't sleep after a download session in case stream is still live
        except Exception as e:
            logging.exception('Process communicate returned error:\n')
        proc = None

if __name__ == '__main__':
    if not twitchClientId or not twitchClientSecret:
        logging.critical('Missing TWITCH_LIVELEECH_CLIENT_ID or TWITCH_LIVELEECH_CLIENT_SECRET env variable(s).')
        os._exit(1)

    main()
