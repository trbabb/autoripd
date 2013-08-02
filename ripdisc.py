#!/usr/bin/python

import os, sys
import subprocess as subp
import glob
import csv
import errno
import tempfile

from procmgmt import DFT_MGR
from common_util import Error, Warn, Msg, Babble, Die, uniquePath

"""
Module for ripping DVDs/Blu-Rays
"""

###########################
# Blu-ray ripping         #
###########################

# meaning of title/stream info codes from makemkvcon
# as defined in makemkvgui/inc/lgpl/apdefs.h
MAKEMKV_ATTRIBUTE_ENUMS = {
    0:  'unknown',
    1:  'type',
    2:  'name',
    3:  'langCode',
    4:  'langName',
    5:  'codecId',
    6:  'codecShort',
    7:  'codecLong',
    8:  'chapterCount',
    9:  'duration',
    10: 'diskSize',
    11: 'diskSizeBytes',
    12: 'streamTypeExtension',
    13: 'bitrate',
    14: 'audioChannelsCount',
    15: 'angleInfo',
    16: 'sourceFileName',
    17: 'audioSampleRate',
    18: 'audioSampleSize',
    19: 'videoSize',
    20: 'videoAspectRatio',
    21: 'videoFrameRate',
    22: 'streamFlags',
    23: 'dateTime',
    24: 'originalTitleId',
    25: 'segmentsCount',
    26: 'segmentsMap',
    27: 'outputFileName',
    28: 'metadataLanguageCode',
    29: 'metadataLanguageName',
    30: 'treeInfo',
    31: 'panelTitle',
    32: 'volumename',
    33: 'orderWeight'
}

def ripBluRay(device, 
              destDir, 
              workingDir, 
              ejectDisc=True,
              procManager=DFT_MGR):
    """Use makemkvcon to rip a blu-ray movie from the given device. 
    <destDir> is the path of the folder into which finished ripped movies 
    will be moved. <tmpDir> is the path of a folder where unfinished rips 
    will reside until they are complete.
    
    Returns path of the ripped media file, or None."""
    
    properties = bluRayDiscProperties(device, procManager)
    if properties is None:
        # failure. brdProperties() will have reported the error.
        return None
    
    disc, titles = properties
    feature_title_id = detectBluRayMainFeature(titles)
    name = disc['name'] if 'name' in disc else 'Unknown Blu-Ray' 
    
    Msg("Ripping title %s of %s to %s" % (feature_title_id, name, tmpdest))
    
    retcode, sout, serr = procManager.call([
                             "makemkvcon",
                             "mkv", 
                             "dev:%s" % device, 
                             str(feature_title_id),
                             workingDir])
    
    if retcode != 0:
        Error("Failed to rip from '%s' %s" % (disc['title'], device))
        Error("makemkvcon output:\n%s" % serr)
        # unfinished mkv laying around for debugging. autoripd will delete the
        # working directory if the user has chosen so with a config setting
        return None
    else: 
        # move tmp mkv to final location
        f_output = titles[feature_title_id]['outputFileName']
        f_output = os.path.join(workingDir, f_output)
        final_filename = "%s.mkv" % name
        final_path = uniquePath(os.path.join(destDir, final_filename))
        os.rename(f_output, final_path)
        
        if ejectDisc:
            # not process logged, but probably safe.
            subp.call(['eject', device])
        
        Msg("Ripped %s successfully" % name)
        return os.path.abspath(final_path)


def bluRayDiscProperties(device, procManager=DFT_MGR):
    """Use makemkvcon to enumerate the properties of a blu-ray movie disc.
    Note that this method may be quite slow due to I/O (probably too slow for an 
    interactive application).
    
    Result is returned like:
        (disc_properties, titles)
    
    where disc_properties is:
        {'property' : value, ... }
    
    and titles is:
        {title_id : {'property' : value, ... ,
                     'streams'  : {stream_id : {'property' : value, ... }}
                    }
        }
    
    Returns None on error.
    """
    
    # get the properties in (almost) csv format from makemkvcon \
    retcode, sout, serr = procManager.call(
                              ['makemkvcon', 
                              '-r', 
                              'info', 
                              'dev:%s' % device])
    
    if retcode != 0:
        Error("Could not acquire blu-ray title info from %s" % device)
        Error("makemkvcon output:\n%s" % serr)
        return None
    else:
        # parse comma-separated messages, accounting for quoted strings.
        # this is one line. I heart python.
        parsed = [x for x in csv.reader(sout.split("\n"))]
        
        disc   = {}
        titles = {}
        for ifo in parsed:
            if len(ifo) == 0:
                # blank line
                continue
            
            # first field contains data after the ":", make it a proper column
            data = ifo[0].split(":", 1) + ifo[1:]
            
            # make integers where possible
            for i, field in enumerate(data):
                try:
                    data[i] = int(field)
                except ValueError:
                    # not an int, apparently
                    pass
            
            key = data[0]
            dst = None
            val = None
            property = None
            if key == 'CINFO':
                # disk info
                property, code, val = data[1:]
                dst = disc
            elif key == 'TINFO':
                # track info
                title, property, code, val = data[1:]
                if title not in titles:
                    titles[title] = {'streams' : {}}
                dst = titles[title]
            elif key == 'SINFO':
                # stream info
                title, stream, property, code, val = data[1:]
                if title not in titles:
                    titles[title] = {'streams' : {}}
                if stream not in titles[title]['streams']:
                    titles[title]['streams'][stream] = {}
                dst = titles[title]['streams'][stream]
            
            if dst is not None and \
                    property in MAKEMKV_ATTRIBUTE_ENUMS:
                dst[MAKEMKV_ATTRIBUTE_ENUMS[property]] = val
        return (disc, titles)


def durationToSeconds(duration):
    """Convert a timecode to raw seconds"""
    parts = map(int, duration.split(":"))
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    elif len(parts) == 2:
        return parts[0] * 60 + parts[1]
    else:
        raise ValueError("could not parse timecode") 


def detectBluRayMainFeature(titles):
    """Attempt to guess the main feature using metrics such as time,
    tracks, number of chapters, etc. Return the number of the detected 
    title. 
    
    May not work quite right, as movie studios like to do crazy shit to try 
    and confuse algorithms like this one. Also, this has not yet been tested 
    on a wide collection of titles."""
    
    title_metrics = []
    
    # 'stream x is of type t' predicate function
    istyp = lambda t: lambda x: 'type' in x and t in x['type'].lower()
    
    # gather info about each title
    for i,t in titles.iteritems():
        streams  = t['streams']
        duration = durationToSeconds(t['duration']) if 'duration' in t else 0
        n_subt   = len(filter(istyp('subtitle'), streams.itervalues()))
        n_audio  = len(filter(istyp('audio'),    streams.itervalues()))
        n_chapt  = t['chapterCount'] if 'chapterCount' in t else 0
        
        title_metrics.append((duration, n_subt, n_audio, n_chapt, i))
    
    # some importance weights that I very 
    # scientifically pulled out of my butt:
    wts = [.81, 0.089, 0.071, 0.030]
    
    # largest of each metric, so we can normalize
    max_of_field = [max([float(x[i]) for x in title_metrics]) 
                    for i in range(len(wts))]
    
    # prune out any titles shorter than 75% of longest title
    title_metrics = filter(lambda x: x[0] > 0.75 * max_of_field[0], title_metrics)
    
    # sort according to weighted sum of buttsourced metrics, highest first
    final_metrics = []
    for row in title_metrics:
        wt = sum([row[i] * wts[i] / max_of_field[i] for i in range(len(wts))])
        final_metrics.append((wt, row[-1]))
    final_metrics.sort(reverse=True)
    
    # the "best"
    return final_metrics[0][-1] 


###########################
# MediaInfo parsing       #
###########################


def mediaInfoData(filename, procManager=DFT_MGR):
    fpath = os.path.abspath(filename)
    
    # get the media info
    # -f means "full", which outputs lots of redundant data in lots of different
    # text formats. This is the only way to get integer data (e.g. for file 
    # sizes, durations, resolution, etc.). We will ignore redundant textual data 
    # if there is numerical data available. 
    retcode, sout, serr = procManager.call(['mediainfo', '-f', fpath])
    
    if retcode != 0:
        Error("Could not obtain media info for %s" % fpath)
        return None
    else:
        mode = None
        track = None
        properties = {'tracks' : []}
        
        for line in sout.split('\n'):
            if ':' not in line:
                # push the last track onto the stack
                if mode != 'General' and track != None and mode != None:
                    # 'General' isn't a track
                    # don't push its (empty) track dictionary
                    properties['tracks'].append(track)
                
                ctgs = ('General', 'Video', 'Audio', 'Text', 'Chapter', 'Menu')
                match = map(line.startswith, ctgs)
                if any(match):
                    # beginning of a new track/category
                    newmode = ctgs[match.index(True)]
                    
                    track = {'type' : newmode.lower()}
                    if newmode == 'Menu':
                        track['items'] = {}
                    
                    mode = newmode
                else:
                    # blank line, e.g.
                    # don't attempt to parse or push tracks until we encounter
                    # a new category
                    mode = None
                
                continue
            else:
                if mode == 'Menu':
                    a, b = map(str.strip, line.split('  :', 1))
                    if ':' in a:
                        dest_dict = track['items']
                        key, val = b.lower(), a
                    else:
                        dest_dict = track
                        key, val = a.lower(), b
                elif mode == None:
                    continue
                else:
                    key, val = map(str.strip, line.split(':', 1))
                    key = key.lower()
                    
                    if mode == 'General':
                        dest_dict = properties
                    else:
                        dest_dict = track
                
                try:
                    val = int(val)
                    dest_dict[key] = val
                except ValueError:
                    # not a number
                    
                    if key == 'language':
                        # hackity hack
                        if len(val) < 2:
                           continue
                        elif len(val) == 3 and val.islower():
                           # use only 3-letter language code
                           dest_dict[key] = val
                        else:
                           dest_dict['language name'] = val
                    
                    # replace only if original is a string
                    elif key not in dest_dict or type(dest_dict[key]) == str:
                        dest_dict[key] = val
        
        return properties


###########################
# DVD ripping             #
###########################


def getIndent(s):
    x = ''
    for c in s:
        if c.isspace():
            x += c
        else:
            break
    return len(x.expandtabs())


def ripDVD(device, 
           destDir, 
           tmpDir, 
           extraOptions=[], 
           ejectDisk=True, 
           procMgr=DFT_MGR):
    Msg("Reading metadata from %s" % device)
    dvd_data = dvdDiscProperties(device, procMgr)
    
    if dvd_data is None:
        return False
    
    name1 = dvd_data['dvd_title']
    name2 = dvd_data['dvd_alt_title']
    
    # find main_feature title
    main_title = 'unknown'
    for title, props in dvd_data['titles'].iteritems():
        if 'main_feature' in props and props['main_feature']:
            main_title = title
    
    if len(name1) > 0:
        name = name1
    elif len(name2) > 0:
        name = name2
    else:
        name = "Unknown DVD" 
    
    tmpfile = uniquePath(os.path.join(tmpDir,"%s.mp4" % name)) 
    
    Msg("Ripping title %s of %s to %s" % (main_title, name, tmpDir))
    
    retcode, sout, serr = procMgr.call(
                               ['HandBrakeCLI',
                                '-i', device,
                                '-o', tmpfile] + extraOptions)
    
    if retcode != 0:
        Error("HandBrake failed to rip title '%s' of disc '%s'" %
               (main_title, name))
        Error("HandBrake output:\n %s" % serr)
        
        # autoripd will clear up the temp directory
        return None
    else:
        # move movie back to destination
        final_file = uniquePath(os.path.join(destDir, "%s.mp4" % name))
        os.rename(tmpfile, final_file)
        if ejectDisk:
            # not process logged, but probably safe.
            subp.call(['eject', device])
        return os.path.abspath(final_file)


#TODO: This fails on amadeus side 2
def dvdDiscProperties(device, procMgr=DFT_MGR):
    """Return the on-disc title, duration, chapters, audio tracks, subtitle 
    tracks, etc. by parsing HandBrakeCLI output. Note that the reported disc 
    title may not reflect the actual movie title (e.g., "SONY")."""
    
    properties = {'titles':{}}
    
    retcode, sout, serr = procMgr.call(
                               ["HandBrakeCLI", 
                                "-t", "0",
                                "-i", device])
    if retcode != 0:
        Error("Unable to obtain DVD info from %s" % device)
        Error("HandBrake output: %s \n\n %s" % (sout, serr))
        return None
    
    # data is hierarchical, delimited by indent.
    # handbrake's data formatting is absolutely abysmal, thus parsing is also 
    # ugly and complex.
    
    # top of this stack is current indent amt
    indentStack = [0]
    # top of this stack is what we add properties to
    dictStack = [properties['titles']]
    # top of this stack is the key (i.e. name) of our parent node
    keyStack = ['']
    
    # why yes, it *does* print valid, normal-operations data to stderr!
    for line in serr.splitlines():
        if len(line.strip()) == 0:
            # blank line
            continue
        if len(dictStack) <= 1:
            # root-level properties
            if 'DVD Title:' in line:
                # example:
                # libdvdnav: DVD Title: AMADEUS_SIDE_A_16X9_LB
                properties['dvd_title'] = line.rsplit(":", 1)[1].strip()
            elif 'DVD Title (Alternative):' in line:
                properties['dvd_alt_title'] = line.rsplit(":", 1)[1].strip()
            elif 'DVD Serial Number' in line:
                properties['dvd_serial_number'] = line.rsplit(":", 1)[1].strip()
        if line.strip().startswith('+'):
            # a property tree node
            
            curIndent = getIndent(line) 
            if curIndent > indentStack[-1]:
                # we've descended into a child node
                indentStack.append(curIndent)
            else:
                # we've popped back up to a parent node
                while curIndent < indentStack[-1]:
                    # pop stuff off the stacks
                    del dictStack[-1]
                    del keyStack[-1]
                    del indentStack[-1]
            
            # try to get a key : value pair
            # strip off the leading '  +'
            trimline = line.lstrip(' +\t\r\n')
            pair = map(str.strip, trimline.split(":",1))
            if len(pair) == 1 and 'Main Feature' in pair[0]:
                dictStack[-1]['main_feature'] = True
                continue
            elif 'track' in keyStack[-1]:
                # a special case. this data is like: 
                # 1, English (AC3) (5.1 ch) (iso639-2: eng), 48000Hz, 384000bps
                # seriously, what is the logic behind this crap
                key, val = map(str.strip, trimline.split(',', 1))
            elif len(pair) == 2:
                key, val = pair
            else:
                # I don't care what this stupid node is, it's not even labeled.
                continue
            
            if len(val) == 0:
                # a parent node. like: 'title 1:'
                # create a new dict for us to add to 
                newDict = {}
                dictStack[-1][key] = newDict
                dictStack.append(newDict)
                keyStack.append(key)
                continue
            
            # now handle the actual data
            # special cases all over the place
            if keyStack[-1] == 'chapters':
                # data like:
                #   cells 0->0, 93287 blocks, duration 00:04:48
                datachunks = map(str.strip, val.split(','))
                datapairs = map(str.split, datachunks)
                val = {'cells' : datapairs[0][1],
                       'blocks' : datapairs[1][0],
                       'duration' : datapairs[2][1]}
            elif key == 'size':
                # data like: 
                # size: 720x480, pixel aspect: 853/720, display aspect: 1.78, 23.976 fps
                datachunks = trimline.split(",")
                datapairs  = [map(str.strip, x.split(':')) for x in datachunks]
                dictStack[-1][datapairs[0][0]] = datapairs[0][1]
                dictStack[-1][datapairs[1][0]] = datapairs[1][1]
                dictStack[-1][datapairs[2][0]] = datapairs[2][1]
                dictStack[-1]['fps']           = datapairs[3][0]
                continue
            
            # store data
            dictStack[-1][key] = val
        else:
            # garbage data
            continue
    
    return properties

