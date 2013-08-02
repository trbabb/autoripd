#!/usr/bin/python

import common_util
from pluginbase import PluginBase

"""
This is an example plugin which does nothing except demonstrate the autoripd 
plugin architecture.

To enable a plugin, add its module name (in this case "example") to the 
'enablePlugins' list in the config.
"""

############################################
# Every plugin module must implement this! #
############################################

def GetPluginClass():
    """Return the class of plugin object to be run by autoripd. Every plugin 
    module must implement this function.""" 
    return ExamplePlugin

####################
# Plugin class     #
####################

class ExamplePlugin(PluginBase):
    
    def processRip(self, mediaFilePath,
                         mediaMetadata, 
                         programSettings,
                         workingDir,
                         previousPluginData):
        """Every plugin instance must implement this method. Parameters:
            - mediaFilePath: 
                Absolute path of the video file created by autoripd
            - mediaMetadata:
                A dictionary containing metadata for the ripped video file
            - programSettings:
                A dictionary of program and plugin configuration settings. 
                Plugin-specific settings will only be included if they are 
                reported in the dictionary returned by getDefaultSettings()
            - previousPluginData:
                Data which was returned by previous plugins' processRip() 
                function will be listed here, in order. This allows plugins to 
                be chained and to pass data to each other. 
            
        This function should return any information that a later plugin might 
        need to do its work.
        """
        
        if programSettings['example_doPrint']:
            import pprint
            print "Running example plugin!"
            print "Processing media file:", mediaFilePath
            print "Media metadata:"
            print  pprint.pformat(mediaMetadata)
            print "Program settings:"
            print  pprint.pformat(programSettings)
        
        return {}
    
    
    @staticmethod
    def getDefaultSettings():
        """Return all the settings this plugin might read and their default 
        values. Settings not listed here will not be passed to the plugin. Every 
        plugin class that accepts custom settings should override this method."""
    
        return {"example_doPrint" : True}
    
