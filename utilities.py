import sublime

def getAllViewsForPath(path):
   return [view for view in (window.find_open_file(path) for window in sublime.windows()) if view is not None]

