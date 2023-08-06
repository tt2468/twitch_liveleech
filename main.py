# twitch_liveleech - Copyright 2022 IRLToolkit Inc.

# Usage: twitch_liveleech.py [channel] [output path]

import sys
channelName = sys.argv[1]
outputPath = sys.argv[2]

import logging
logging.basicConfig(level=logging.DEBUG, handlers=[logging.FileHandler('twitch_ll_{}.log'.format(channelName)), logging.StreamHandler()], format="%(asctime)s [%(levelname)s] %(message)s")

import os
import string
import time
import datetime
import requests
import streamlink
import ffmpeg

CHECK_SLEEP_DURATION = 45 # Seconds
FMP4_FRAGMENT_DURATION = 60 # Seconds

twitchClientId = os.getenv('TWITCH_LIVELEECH_CLIENT_ID')
twitchClientSecret = os.getenv('TWITCH_LIVELEECH_CLIENT_SECRET')
twitchApiHeader = os.getenv('TWITCH_LIVELEECH_API_HEADER') or ''

months = ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec']

def append_file(fileName, data):
    with open(fileName, 'a') as f:
        f.write('\n======================================================================\n')
        f.write(data.decode())

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
    path = '{}/{}_{}_{}.mp4'.format(dir, date.day, title, int(time.time()))
    return path

if __name__ == '__main__':
    if not twitchClientId or not twitchClientSecret:
        logging.critical('Missing TWITCH_LIVELEECH_CLIENT_ID or TWITCH_LIVELEECH_CLIENT_SECRET env variable(s).')
        os._exit(1)

    session = streamlink.session.Streamlink()
    options = streamlink.options.Options()
    options.set('disable-hosting', True)
    options.set('disable-ads', True)
    options.set('disable-reruns', True)
    if twitchApiHeader:
        options.set('api-header', {'Authorization': twitchApiHeader})
    _, pluginClass, resolvedUrl = session.resolve_url('https://twitch.tv/{}'.format(channelName))
    plugin = pluginClass(session, resolvedUrl, options)

    while True:
        logging.debug('Sleeping for {} seconds...'.format(CHECK_SLEEP_DURATION))
        time.sleep(CHECK_SLEEP_DURATION)
        logging.debug('Done.')

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
        stream = ffmpeg.input(streams['best'].url).output(path, vcodec = 'copy', acodec = 'aac', frag_duration = 1000000 * FMP4_FRAGMENT_DURATION, movflags = 'empty_moov+delay_moov')
        try:
            out, err = ffmpeg.run(stream, capture_stdout=True, capture_stderr=True)
            append_file('twitch_ll_download_{}.log'.format(channelName), err)
            logging.info('Stream ended!')
        except:
            logging.exception('FFmpeg library returned error:\n')
