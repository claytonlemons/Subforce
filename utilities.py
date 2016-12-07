import sublime

def getAllViewsForPath(path):
   return [view for view in (window.find_open_file(path) for window in sublime.windows()) if view is not None]

def coercePathsToActiveViewIfNeeded(paths, window):
   return paths if paths else [window.active_view().file_name()]

def getRevisionQualifiedDepotPath(file, revision):
   return "{file}#{revision}".format(file=file, revision=revision)