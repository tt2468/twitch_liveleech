# twitch_liveleech - Copyright 2022 IRLToolkit Inc.

# Usage: twitch_liveleech.py [channel] [dump path] [final path]

import sys
channelName = sys.argv[1]
downloadPath = sys.argv[2]
finalPath = sys.argv[3]

import logging
logging.basicConfig(handlers=[logging.FileHandler('twitch_ll.log'), logging.StreamHandler()], level=logging.INFO, format="%(asctime)s [%(levelname)s] [{}] %(message)s".format(channelName))

import os
import string
import time
import datetime
import requests
import streamlink
import ffmpeg

twitchClientId = os.getenv('TWITCH_LIVELEECH_CLIENT_ID')
twitchAuthorization = os.getenv('TWITCH_LIVELEECH_AUTHORIZATION')

months = ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec']
sleepDuration = 45

sl = streamlink.Streamlink()
sl.set_plugin_option('twitch', 'disable-hosting', True)
sl.set_plugin_option('twitch', 'disable-ads', True)
sl.set_plugin_option('twitch', 'disable-reruns', True)

def append_file(fileName, data):
    with open(fileName, 'a') as f:
        f.write('\n')
        f.write(data.decode())

def get_channel_title():
    headers = {'Client-ID': twitchClientId, 'Authorization': 'Bearer ' + twitchAuthorization}
    req = requests.get('https://api.twitch.tv/helix/users?login={}'.format(channelName.lower()), headers=headers)
    if req.status_code != requests.codes.ok:
        logging.warning('Failed to get channel title due to HTTP error. Code: {} | Text: {}'.format(req.status_code, req.text))
        return 'UNKNOWN TITLE'
    channelId = req.json()['data'][0]['id']
    req = requests.get('https://api.twitch.tv/helix/channels?broadcaster_id={}'.format(channelId), headers=headers)
    if req.status_code != requests.codes.ok:
        logging.warning('Failed to get channel title due to HTTP error. Code: {} | Text: {}'.format(req.status_code, req.text))
        return 'UNKNOWN TITLE'
    data = req.json()
    return data['data'][0]['title']

def check_generate_path(pathPrefix):
    date = datetime.date.today()
    dir = '{}/{}_{}'.format(pathPrefix, months[date.month - 1], date.year)
    if not os.path.exists(dir):
        logging.info('Creating directory: {}'.format(dir))
        os.makedirs(dir)

if __name__ == '__main__':
    if not twitchClientId or not twitchAuthorization:
        logging.critical('Missing TWITCH_LIVELEECH_CLIENT_ID or TWITCH_LIVELEECH_AUTHORIZATION env variable(s).')
        os._exit(1)

    while True:
        logging.info('Sleeping for {} seconds...'.format(sleepDuration))
        time.sleep(sleepDuration)
        logging.info('Done.')

        try:
            streams = sl.streams('https://twitch.tv/{}'.format(channelName))
        except streamlink.exceptions.PluginError:
            logging.error('Failed to fetch stream via streamlink.')
            continue
        if not streams:
            logging.info('No streams are available.')
            continue
        elif 'best' not in streams:
            logging.error('`best` stream not available!')
            break
        logging.info('Stream found! Opening ffmpeg...')

        fullDownloadPath = '{}/{}.flv'.format(downloadPath, int(time.time()))
        logging.info('Writing download to: {}...'.format(fullDownloadPath))
        stream = ffmpeg.input(streams['best'].url).output(fullDownloadPath, vcodec='copy', acodec='aac')
        out, err = ffmpeg.run(stream, capture_stdout=True, capture_stderr=True)
        append_file('twitch_ll_download.log', err)
        logging.info('Stream ended!')

        check_generate_path(finalPath)

        title = get_channel_title()
        validChars = "-.() %s%s" % (string.ascii_letters, string.digits)
        title = ''.join(c for c in title if c in validChars)

        date = datetime.date.today()
        fullPath = '{}/{}_{}/{}_{}_{}.mp4'.format(finalPath, months[date.month - 1], date.year, date.day, title, int(time.time()))
        logging.info('Muxing file {} to final path {}'.format(fullDownloadPath, fullPath))
        mux = ffmpeg.input(fullDownloadPath).output(fullPath, vcodec='copy', acodec='copy')
        out, err = ffmpeg.run(mux, capture_stdout=True, capture_stderr=True)
        append_file('twitch_ll_mux.log', err)
        logging.info('Done.')
