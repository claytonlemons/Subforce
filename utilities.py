import sublime
import os

def getAllViewsForPath(path):
   return [view for view in (window.find_open_file(path) for window in sublime.windows()) if view is not None]

def coercePathsToActiveViewIfNeeded(paths, window):
   return paths if paths else [window.active_view().file_name()]

def getRevisionQualifiedDepotPath(file, revision):
   return "{file}#{revision}".format(file=file, revision=revision)

def checkForAndGetSinglePath(paths):
      if len(paths) == 0 or len(paths) > 1:
         sublime.error_message("A graphical diff of depot revisions can only be performed on one workspace file at a time.")
         return None
      else:
         return paths[0]

def ellipsizeIfDirectory(path):
   return os.path.join(path, '...') if os.path.isdir(path) else path

createRevision = lambda revision, description: {'revision': revision, 'desc': description}