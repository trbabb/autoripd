import procmgmt

"""
All plugin modules should expose a subclass of PluginBase.
"""

class PluginBase:
    
    def __init__(self, procmgr=procmgmt.DFT_MGR):
        self.procmgr = procmgr
    
    def processRip(self, mediaFilePath,
                         mediaMetadata, 
                         programSettings, 
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
        
        raise NotImplementedError("processRip not implemented")
    
    
    @staticmethod
    def getDefaultSettings():
        """Return all the settings this plugin might read and their default 
        values. Settings not listed here will not be passed to the plugin. Every 
        plugin class that accepts custom settings should override this method."""
        return {}
    
    
    def getProcessManager(self): 
        """Return a manager object which can be used to invoke other programs 
        via processManager.call([...]). This is preferable to using the 
        subprocess module (which processManager wraps), since this object 
        handles logging and safe process termination when the daemon exits.""" 
        
        return self.procmgr


