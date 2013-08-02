#!/usr/bin/python

import os
import common_util
import tempfile
import shutil
import subprocess as subp
from pluginbase import PluginBase

"""
This plugin remuxes a ripped video to .m2ts in a way that ensures the ps3 
can play it natively. 

Requires a tsMuxeR installation. If subtitle extraction is enabled (via 
mux_subtitleWorkaround), then an up-to-date installation of mkvextract is also 
required. If DTS to AC3 re-encoding is enabled (via transcodeDTS), then 
up-to-date installations of aften and dcadec are both required.

A note about subtitles: As of the time of writing, the PS3 does not currently 
support streaming subtitles. However, this plugin makes it possible to correctly 
store the streams in the .m2ts file, even if the ps3 cannot make use of them. 
This way, the subtitles still viewable in other media players which support 
them (e.g. VLC), and may be viewable someday on the PS3 if streaming subtitle 
support is ever added (don't hold your breath).


Configuration settings:
    mux_preserveSrc (bool):
      Should the original ripped file be deleted after remuxing?
    mux_m2tsDir (str):
      Where to put the newly-generated .m2ts file. If not set, the new .m2ts 
      will go in the same directory as the original ripped movie.
    mux_srcBackupDir (str):
      If set, where to move the original ripped file for backup. If not set, the 
      original file will not be moved or backed up. 
    mux_subtitleWorkaround (bool):
      If enabled, enure that PGS streams are muxed from an intermediate file 
      instead of directly from MKV. This is a workaround for the issues tsMuxer 
      has with subtitles.
    mux_subtitleLangs (list(str)):
      List of 3-letter lowercase language codes; only subtitle tracks matching 
      them will be saved. If not set, all subtitle tracks will be saved.
    mux_transcodeDTS (bool):
      If set, will re-encode DTS audio tracks as AC3. Playstation3 does not 
      accept network-streamed DTS tracks; this works around that restriction.
      Requires 'aften' and 'dcadec' to be installed.
    mux_dtsBitrate (int):
      If using transcodeDTS, the bitrate in Kbps at which to encode AC3 audio. 
      Default is the maximum standard of 640Kbps.
    mux_dtsBandwidth (int):
      If using transcodeDTS, the bandwidth at which to encode AC3 audio. 
      Default is the maximum setting of 60. Higher numbers mean higher frequency 
      cutoff (and thus higher quality). -1 is "automatic". -2 is "variable". See 
      the aften manual for more details.
    mux_transcodeVC1 (bool):
      If set to True, then VC-1 video streams will be transcoded to H.264 using 
      HandBrake. This is to bypass the restriction that PS3s cannot play VC-1 
      video from file.
    mux_twoPassEncode (bool):
      If set to True, then VC-1 -> AVC transcoding will use a 2-pass 
      approach. This allows for more efficient, high-quality compression, 
      but may take longer. Defaults on.
    mux_turboFirstPass (bool):
      If using mux_twoPassEncode, the first (scanning) pass will use 
      speed optimizations, in exchange for a very small impact on quality.
    mux_h264bitrate (int):
      Bitrate at which to transcode VC1 video to H.264 in Kbps. If unset, the 
      same bitrate as the source will be used.
    mux_x264opts (dict(string)):
      Dictionary of x264 encoding option-to-value mappings. The defaults have 
      been selected to ensure playback on a PS3. For a description of available 
      options, see http://mewiki.project357.com/wiki/X264_Settings
    mux_audioLanguages (list(str)):
      If set, audio tracks not matching the 3-letter language codes in this list 
      will be discarded. This can be used to save space and transcoding time if, 
      for example, only English audio is desired. If not set, all tracks will be 
      included.
    mux_allowedCodecs (list(str)):
      Only mux streams using codecs matching the names given in this list. 
      This is used to ensure we only ask tsMuxeR to mux compatible streams.
    mux_tsMuxer (str):
      Command to run tsMuxeR. Set this if tsMuxeR is not in your search path.
    mux_mkvextract (str):
      Command to run mkvextract. Set this if mkvextract is not in your search 
      path.
    mux_dcadec (str):
      Command to run dcadec. Set this if dcadec is not in your search path.
    mux_aften (str):
      Command to run aften. Set this if aften is not in your search path.

"""

# TODO: 2-pass x264 encoding?
# TODO: report new location/deletion of source
# TODO: handle vc-1 content somehow
#       Notes on h264 / ps3 compatibility:
# http://www.digital-digest.com/articles/PS3_H.264_Conversion_Guide_page4.html
# TODO: move vc-1 processing to post-extraction

# TODO: dcadec spews a billion "skip" messages for No Good Reason. It would be
#       nice to pipe these and trim them out, displaying only relevant messages.
#       I am not sure how to do this (i.e. pipe between two programs, run them 
#       both, AND read from the stdout/err of sboth) without risking deadlock.

class struct(object):
    pass


#########################
# Plugin factory method #
#########################

def GetPluginClass():
    """Return the class of the plugin object to be run by autoripd. Every plugin 
    module must implement this function."""
    return RemuxPlugin

####################
# Plugin class     #
####################

class RemuxPlugin(PluginBase):
    
    def processRip(self, mediaFilePath,
                         mediaMetadata,
                         programSettings,
                         workingDir,
                         previousPluginData):
        """Remux a rip into an .m2ts file."""
        
        self.assignSettings(programSettings)
        self.origFile = mediaFilePath
        
        # should we perform the mux?
        media_base = os.path.basename(mediaFilePath)
        fname, ext = os.path.splitext(media_base)
        if mediaMetadata['format'] != 'Matroska':
            return {}
        
        # find a name/place for the new file
        outdir = self.m2tsDir
        if outdir is None:
            # ship .m2ts files to the same place as the other rips
            outdir = self.autoripd_settings.destDir
        if not os.path.exists(outdir):
            os.makedirs(outdir)
        outname = fname + ".m2ts"
        outfile = common_util.uniquePath(os.path.join(outdir, outname))
        
        # perform the mux
        newfile = self.remux(mediaFilePath, outfile, mediaMetadata, workingDir)
        
        # backup and/or delete the original source
        if newfile is not None:
            bkupdir = self.srcBackupDir
            if bkupdir is not None and \
                  self.preserveSrc and \
                  not os.path.samefile(bkupdir, os.path.dirname(mediaFilePath)):
                
                if not os.path.isdir(bkupdir):
                    os.path.makedirs(bkupdir)
                dstpath = common_util.uniquePath(os.path.join(bkupdir, media_base))
                os.rename(mediaFilePath, dstpath)
            elif not self.preserveSrc:
                os.unlink(mediaFilePath)
                common_util.Msg("Removed %s" % mediaFilePath) 
        else:
            raise Exception("remuxer encountered a problem; aborted")
        
        return {'mux_new_m2tsfile' : newfile}
    
    
    @staticmethod
    def getDefaultSettings():
        """Default settings for Remuxer plugin."""
        return {
            'mux_preserveSrc'        : True,
            'mux_m2tsDir'            : None, 
            'mux_srcBackupDir'       : None,
            
            'mux_tsMuxeR'            : 'tsMuxeR',
            'mux_mkvextract'         : 'mkvextract',
            'mux_dcadec'             : 'dcadec',
            'mux_aften'              : 'aften',
            
            'mux_subtitleWorkaround' : True,
            'mux_subtitleLangs'      : [], # TODO: revert to None (TEST)
            
            'mux_transcodeDTS'       : True,
            'mux_dtsBitrate'         : 640,  # 640K ought to be enough for anybody
            'mux_dtsBandwidth'       : 60,   # phasers at maximum
            'mux_preserveDTS'        : False,
            'mux_audioLanguages'     : None,
            
            'mux_transcodeVC1'       : True,
            'mux_h264bitrate'        : None,
            'mux_twoPassEncode'      : True,
            'mux_turboFirstPass'     : True,
            'mux_x264opts'           : 
            
                {
                  'vbv-maxrate'   : 40000, # per blu-ray spec maximum.
                                           # some websites say this should
                                           # be as low as 20k for PS3
                  'vbv-bufsize'   : 30000,
                  'level'         : '4.0', # TODO: can this be raised to 4.1? (TEST)
                  'keyint'        : 24,
                  'slices'        : 4,
                  'bluray-compat' : 1,
                  
                  # Experimental:
                  'bframes'       : 3,
                  'nal-hrd'       : 'vbr',
                  'b-pyramid'     : 'none', #not supported by ps3
                  'aud'           : 1,
                  'colorprim'     : 'bt709',
                  'transfer'      : 'bt709',
                  'colormatrix'   : 'bt709',
                },
            
            'mux_allowedCodecs'      : 
            
                [
                    'V_MPEG4/ISO/AVC',
                    'V_MS/VFW/WVC1', 'WVC1',
                    'V_MPEG-2', 'V_MPEG2',
                    'A_AC3',
                    'A_DTS',
                    'A_MP3',
                    'A_LPCM',
                    'S_HDMV/PGS',
                    'S_TEXT/UTF8'
                ]
        }
    
    
    #################################
    # remux plugin-specific methods #
    #################################
    
    
    def assignSettings(self, settings):
        """For convenience, we make all mux config settings into attributes of
        this ojbect."""
        
        s = struct()
        for k,v in settings.iteritems():
            if k.startswith("mux_"):
                # strip the "mux_" off the setting name
                attrname = k[4:]
                if hasattr(self, attrname):
                    common_util.Warn("settings key collision: self already "
                        "has attr '%s' (setting '%s')" % (attrname, k))
                else:
                    # this setting becomes an attribute of self
                    setattr(self, attrname, v)
            else:
                setattr(s, k, v)
        
        # all non-plugin specific settings get assigned to a dedicated object:
        self.autoripd_settings = s
    
    
    def remux(self, infile, outfile, info, workingdir):
        """Remux <infile> into an .m2ts at <outfile>, returning the complete 
        path of <outfile> upon success, or None upon failure."""
        
        fpath    = os.path.abspath(infile)
        outfpath = os.path.abspath(common_util.uniquePath(outfile))
        if not os.path.isfile(fpath):
            common_util.Error('File %s could not be found for remuxing' % fpath)
            return None
        titlename = os.path.splitext(info['file name'])[0]
        
        tkProcessData = {}
        
        # generate the .meta file
        # per spec at http://forum.doom9.org/archive/index.php/t-142559.html
        meta = "MUXOPT --no-pcr-on-video-pid --new-audio-pes --vbr --vbv-len=500\n"
        for track in info['tracks']:
            if 'unique id' not in track or 'codec id' not in track:
                # not a mux-able track; i.e. a menu track
                continue
                
            id = track['unique id']
            
            result = self.processTrack(titlename, fpath, track, workingdir)
            if result is None:
                continue
            else:
                meta += result.metaline
                tkProcessData[id] = result
        
        # do the remux
        with tempfile.NamedTemporaryFile(mode='w', 
                                         suffix='.meta', 
                                         dir=workingdir) as tmpf:
            # write metafile
            metaname = os.path.abspath(tmpf.name)
            tmpf.write(meta)
            tmpf.flush()
            
            # extract/process streams
            ok = self.extractTracks(fpath, tkProcessData)
            if not ok:
                return None
            
            common_util.Msg("Remuxing %s to %s" % (fpath, outfpath))
            common_util.Babble("Metafile:\n%s\n" % meta)
            
            # do remux
            mgr = self.getProcessManager()
            retcode, sout, serr = mgr.call([self.tsMuxeR, metaname, outfpath])
            if retcode != 0:
                common_util.Error('Failure to remux %s to %s' % 
                                 (infile, outfpath))
                common_util.Msg('tsMuxeR output: %s\n%s' % (sout,serr))
                return None
            
            # clean up extracted tracks / extra files:
            for trackdat in tkProcessData.itervalues():
                if trackdat.extractTo and os.path.exists(trackdat.extractTo):
                      os.unlink(trackdat.extractTo)
                for extraf in trackdat.cleanupFiles:
                    if os.path.exists(extraf):
                        os.unlink(extraf)
            
            # .meta tmpfile is deleted automatically
        
        common_util.Msg("%s remuxed successfully" % outfpath)
        
        return outfpath
    
    
    def extractTracks(self, srcfile, trackProcessData):
        procMgr = self.getProcessManager()
        files = []
        for id, t in trackProcessData.iteritems():
            if t.extractTo is not None:
                files.append("%s:%s" % (id, t.extractTo))
        
        if len(files) > 0: 
            common_util.Msg("Extracting tracks from %s" % srcfile)
            cmd = [self.mkvextract, 'tracks', srcfile] + files
            retcode, sout, serr = procMgr.call(cmd)
            
            if retcode != 0:
                common_util.Error("Failure to extract track data from %s" % srcfile)
                common_util.Msg("mkvextract output: %s\n%s" % (sout, serr))
                return False
        
        for dat in trackProcessData.itervalues():
            if hasattr(dat.doOnExtracted, '__call__'):
                if not dat.doOnExtracted(dat):
                    # error condition
                    # autoripd will clean up the working dir
                    return False
        return True
    
    
    def processTrack(self, titlename, srcfile, track, workingdir):
        """Return the .meta file line associated with this track, an optional 
        filename to extract the track to, and a function (or None) representing 
        an action to perform on the extracted track. Return None if this track 
        should be skipped.
        
        Track extraction should not be performed here; we extract tracks all
        at once when track analysis is complete (it's faster this way)-- then 
        describe any processing on the extracted tracks with a callback."""
        
        # we'll return this object:
        tkinfo = struct()
        tkinfo.metaline      = ""    # this track's data for the .meta file
        tkinfo.extractTo     = None  # to what file shall this track be extracted?
        tkinfo.doOnExtracted = None  # fn to call when extraction complete
                                     # (will be passed the tkinfo object)
                                     # (shall return false on error, true otherwise)
        tkinfo.cleanupFiles  = set() # extra files to delete after mux completed
        tkinfo.metadata = track
        
        codec = track['codec id']
        id    = track['unique id'] 
        
        if codec not in self.allowedCodecs:
            common_util.Warn("Not muxing track %s; codec '%s' not recognized" %\
                             (id, codec))
            return None
        
        if track['type'] == 'video': 
            tracksrc = srcfile
            trackid  = track['unique id']
            
            # differences in naming convention
            if codec == 'V_MPEG2':
                codec = 'V_MPEG-2'
            elif codec == 'WVC1' or codec == 'V_MS/VFW/WVC1':
                if self.transcodeVC1:
                    h264name = "%s.%s.x264.mkv" % (titlename, id)
                    h264dest = common_util.uniquePath(
                                  os.path.join(workingdir, h264name)) 
                    tracksrc = h264dest
                    trackid  = 1
                    codec    = 'V_MPEG4/ISO/AVC'
                    
                    self.doTranscodeVC1(srcfile, h264dest, track)
                    
                    tkinfo.cleanupFiles.add(h264dest)
                else:
                    codec = 'V_MS/VFW/WVC1'
            
            # extra flags for H.264
            if codec == 'V_MPEG4/ISO/AVC':
                extra = ', insertSEI, contSPS'
            else:
                extra = ''
            
            framerate = track['frame rate'].rsplit('fps', 1)[0].strip()
            
            template = '%s, "%s", fps=%s, track=%s, lang=%s%s\n'
            tkinfo.metaline = template % (codec, 
                                          tracksrc, 
                                          framerate,
                                          trackid, 
                                          track['language'], 
                                          extra)
        elif track['type'] in ('audio','text'):
            if self.subtitleLangs is not None and \
                    track['type'] == 'text' and \
                    track['language'] not in self.subtitleLangs:
                # skip this language
                return None
            elif self.audioLanguages is not None and \
                    track['type'] == 'audio' and \
                    track['language'] not in self.audioLanguages:
                # skip this language
                return None
            
            if codec == 'S_HDMV/PGS' and self.subtitleWorkaround:
                # invent a .sup file name
                # save it for when we perform extraction
                # (we will extract them all at once)
                pgsname  = "%s.%s.%s.pgs" % (titlename, track['language'], id)
                tracksrc = common_util.uniquePath(
                              os.path.join(workingdir, pgsname))
                tkinfo.extractTo = tracksrc
                tracktag = '' # not needed; src file only has one track
            elif codec == 'A_DTS' and self.transcodeDTS:
                # we extract to one file (.dts), and mux from another (the 
                # eventual converted .ac3). the meta file must reflect this
                dtsname  = "%s.%s.%s.dts" % (titlename, track['language'], id)
                ac3name  = "%s.%s.%s.ac3" % (titlename, track['language'], id)
                tracksrc = common_util.uniquePath(
                              os.path.join(workingdir, ac3name))
                tkinfo.extractTo = common_util.uniquePath(
                              os.path.join(workingdir, dtsname))
                tkinfo.ac3file = tracksrc # extra info for the callback
                tkinfo.doOnExtracted =  self.doTranscodeDTS
                codec = 'A_AC3'
                tracktag = ''
            else:
                tracksrc = srcfile
                tracktag = ', track=%s' % id
            
            lang = (', lang=%s' % track['language']) if 'language' in track else ''
            tkinfo.metaline = '%s, "%s"%s%s\n' % (codec, tracksrc, tracktag, lang)
        
        return tkinfo
    
    
    def doTranscodeDTS(self, tkinfo):
        pman     = self.getProcessManager()
        metadata = tkinfo.metadata
        dtsfile  = tkinfo.extractTo
        ac3file  = tkinfo.ac3file
        
        common_util.Msg("Encoding DTS track %s to AC3" % metadata['unique id'])
        
        try:
            devnull = open(os.devnull, 'w')
            dcadec_cmd = [self.dcadec, '-o', 'wavall', dtsfile]
            
            # we directly pipe the decoded stream to aften for encoding to avoid
            # writing an enormous 2-hour 6-channel WAV file to disk
            with pman.Popen(dcadec_cmd, 
                            stdout=subp.PIPE,
                            # really important; dcadec spews 'skip' x 1 billion
                            stderr=devnull) as decodp:
                
                aften_cmd = [self.aften, '-b', str(self.dtsBitrate),
                             '-w', str(self.dtsBandwidth),
                             '-v', '0',
                             '-', ac3file]
                
                with pman.Popen(aften_cmd,
                                stderr=subp.PIPE,
                                stdout=subp.PIPE,
                                stdin=decodp.stdout) as encodp:
                    decodp.stdout.close() # release our handle on this pipe
                    e_sout, e_serr = encodp.communicate()
                    d_ret = decodp.wait()
                    e_ret = encodp.returncode
            devnull.close()
        except OSError, err:
            if err.errno == errno.ENOENT:
                Error("Trouble launching program while transcoding DTS. "
                      "Are dcadec and aften installed?")
            try:
                devnull.close()
            except:
                pass
            raise
        
        common_util.Babble("aften output:\n%s\n%s" % (e_sout, e_serr))
        
        if e_ret != 0 or d_ret != 0 or not os.path.isfile(ac3file):
            common_util.Error("Error re-encoding DTS track")
            return False
        else:
            os.unlink(dtsfile)
            return True
    
    
    def doTranscodeVC1(self, srcfile, outfile, trackinfo):
        id = trackinfo['unique id']
        if self.h264bitrate is None:
            # use source stream bitrate
            # convert bits/sec to Kbits/sec
            bitrate = str(int(trackinfo['bit rate'] / 1000.))
        else:
            bitrate = str(self.h264bitrate)
        
        framerate = trackinfo['frame rate'].split()[0]
        
        encopts = ":".join(
                         ["%s=%s" % tuple(map(str, i))
                          for i in self.x264opts.iteritems()])
        
        # TODO: necessary? (TEST)
        encopts += ":fps=%s" % framerate # really force this
        
        txcode_cmd = ['HandBrakeCLI', 
                      '-i', srcfile,
                      '-o', outfile, 
                        # no audio
                      '-a', 'none',
                      '-e', 'x264',
                      '--width',  str(trackinfo['width']),
                      '--height', str(trackinfo['height']),
                      '-b', bitrate,
                      '-r', framerate,
                      '-x', encopts]
        
        if self.twoPassEncode:
            txcode_cmd += ['--two-pass']
            if self.turboFirstPass:
                txcode_cmd += ['--turbo']
        
        printFriendlyCmd = " ".join(txcode_cmd)
        
        common_util.Msg("Transcoding video track %s to H.264" % id)
        common_util.Babble("Video endoding cmd: %s" % printFriendlyCmd)
        
        mgr = self.getProcessManager()
        retcode, sout, serr = mgr.call(txcode_cmd)
        
        if retcode == 0:
            common_util.Msg("Transcode complete.")
        else:
            raise subp.CalledProcessError(retcode, printFriendlyCmd)
    
    
    def checkSpace(self, srcfile):
        #TODO: actually call this
        #TODO: engineer a consistent framework for calling this.
        srcdir  = os.path.dirname(srcfile)
        srcsize = os.path.getsize(srcdir)
        st      = os.statvfs(srcdir)
        avail   = st.f_bavail * st.f_frsize
        
        # we will need space for the both final remuxed movie and the 
        # intermediate re-encoding. Each of these may be somewhat larger
        # than the source, so we demand an extra 25% for safety.
        return avail > 2.25 * srcsize
