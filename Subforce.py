# Useful documentation:
# Sublime Plugin Framework: http://docs.sublimetext.info/en/latest/reference/plugins.html
# Sublime Plugin Python API: http://www.sublimetext.com/docs/3/api_reference.html
# Perforce API: https://www.perforce.com/perforce/r16.1/manuals/cmdref
# Perforce Python API: https://www.perforce.com/perforce/doc.current/manuals/p4script/03_python.html
# Example Plugin: https://github.com/SideBarEnhancements-org/SideBarEnhancements/blob/st3/SideBar.py

import sublime
import sublime_plugin
import P4
import os
import threading
from .utilities import getAllViewsForPath

DEFAULT_CHANGELIST_NAME = "default"
FILE_CHECKED_OUT_SETTING_KEY = "subforce_file_checked_out"
FILE_NOT_IN_DEPOT_SETTING_KEY = "subforce_file_not_in_depot"
CHANGELIST_NUMBER_STATUS_KEY = "subforce_changelist_number"

p4 = None

def plugin_loaded():
   global p4
   p4 = P4.P4()
   # @TODO: make these user settings
   p4.cwd = 'P:/'
   p4.exception_level = 1 # only errors are raised as exceptions
   # @TODO: pull in user settings for client, port, user...
   p4.connect()
   print("Loaded!")

def plugin_unloaded():
   global p4
   p4.disconnect()
   print("unloaded!")

class SubforceDisplayChangelistDescriptionCommand(sublime_plugin.TextCommand):
   def run(self, edit, description = ""):
      # Enable editing momentarily to set description
      self.view.set_read_only(False)

      self.view.replace(edit, sublime.Region(0, self.view.size()), description)
      self.view.sel().clear()

      self.view.set_read_only(True)

class ChangelistDescriptionOutputPanel(object):
   outputPanelName = 'changelistDescriptionOutputPanel'
   qualifiedOutputPanelName = 'output.changelistDescriptionOutputPanel'
   outputPanelCreationLock = threading.Lock()

   def __init__(self, window):
      self.outputPanelCreationLock.acquire(blocking=True, timeout=1)

      self.window = window

      self.changelistDescriptionOutputPanel = self.window.find_output_panel(self.outputPanelName)
      if not self.changelistDescriptionOutputPanel:
         self.changelistDescriptionOutputPanel = self.window.create_output_panel(self.outputPanelName, True)
         self.changelistDescriptionOutputPanel.settings().set("isChangelistDescriptionOutputPanel", True)

      self.outputPanelCreationLock.release()

   def show(self, description):
      self.window.run_command(
         "show_panel",
         {
            "panel": self.qualifiedOutputPanelName
         }
      )

      self.changelistDescriptionOutputPanel.run_command(
         "subforce_display_changelist_description",
         {
            "description": description
         }
      )

   def hide(self):
      self.window.run_command(
         "hide_panel",
         {
            "panel": self.qualifiedOutputPanelName,
            "cancel": False
         }
      )


class ChangelistManager(object):

   def __init__(self, window):
      self.window = window
      self.changelistDescriptionOutputPanel = ChangelistDescriptionOutputPanel(self.window)

   def viewAllChangelists(self, window, onDoneCallback, includeDefault=False):
      changelists = p4.run("changes", "-c", p4.client, "-s", "pending", "-L")

      def onDone(selectedIndex):
         print("Selected: {}".format(selectedIndex))
         self.changelistDescriptionOutputPanel.hide()
         selectedChangelistNumber = changelists[selectedIndex]['change'] if selectedIndex >= 0 else None
         if onDoneCallback:
            onDoneCallback(selectedChangelistNumber)
         SubforceStatusUpdatingEventListener.updateStatus(self.window.active_view())


      def onHighlighted(selectedIndex):
         fullDescription = p4.fetch_change(changelists[selectedIndex]['change'])._description
         self.changelistDescriptionOutputPanel.show(fullDescription)

      changelistItems = [[changelist['change'], changelist['desc']] for changelist in changelists]

      if includeDefault:
         changelistItems = [[DEFAULT_CHANGELIST_NAME, ""]] + changelistItems

      window.show_quick_panel(
         changelistItems,
         onDone,
         sublime.KEEP_OPEN_ON_FOCUS_LOST,
         0,
         onHighlighted
      )

   def createChangelist(self):
      self.editChangelist(None)

   def editChangelist(self, changelistNumber):
      if changelistNumber:
         p4.run_change(changelistNumber)
      else: # create a new changelist
         p4.run_change()

   def deleteChangelist(self, changelistNumber):
      p4.run_change("-d", changelistNumber)

   def moveToChangelist(self, changelistNumber, file):
      p4.run_reopen("-c", changelistNumber, file)


class SubforceAutoCheckoutEventListener(sublime_plugin.EventListener):
   def on_pre_save(self, view):
      fileName = view.file_name()
      settings = view.settings()
      if not fileName or \
         settings.get(FILE_NOT_IN_DEPOT_SETTING_KEY, False) or \
         settings.get(FILE_CHECKED_OUT_SETTING_KEY, False):
         return

      try:
         stat = p4.run_fstat(fileName) # check if file is in depot
         if "action" in stat[0]:
            # Cache this setting, so we don't run fstat unnecessarily
            settings.set(FILE_CHECKED_OUT_SETTING_KEY, True)
            return
      except Exception as e:
         # More caching!
         settings.set(FILE_NOT_IN_DEPOT_SETTING_KEY, True)
         return

      checkoutFile = sublime.ok_cancel_dialog(
         "You are saving a file in your depot. Do you want to check it out first?",
         "Checkout"
      )

      if checkoutFile:
         # Because Sublime's show_quick_panel is non-blocking, we cannot use it to acquire the user's desired
         # changelist before checking out the actual file. Instead, we check out the file first and then move it to
         # the user's desired changelist.
         p4.run_edit(fileName)
         view.settings().set(FILE_CHECKED_OUT_SETTING_KEY, True)

         view.window().run_command(
            "subforce_move_to_changelist",
            {
               "paths": [fileName]
            }
         )

   @classmethod
   def eraseAutoCheckoutEventListenerSettings(self, view):
      settings = view.settings()
      settings.erase(FILE_CHECKED_OUT_SETTING_KEY)
      settings.erase(FILE_NOT_IN_DEPOT_SETTING_KEY)

   def on_load(self, view):
      self.eraseAutoCheckoutEventListenerSettings(view)

class SubforceStatusUpdatingEventListener(sublime_plugin.EventListener):
   # Some of these may be redundant. Meh.
   def on_activated(self, view):
      self.updateStatus(view)

   def on_deactivated(self, view):
      self.updateStatus(view)

   def on_post_window_command(self, window, commandName, args):
      if commandName.startswith("subforce"):
         self.updateStatus(window.active_view())

   @classmethod
   def updateStatus(self, view):
      try:
         stat = p4.run_fstat(view.file_name())[0] # check if file is in depot
      except:
         return

      if "change" in stat:
         view.set_status(
            CHANGELIST_NUMBER_STATUS_KEY,
            "Changelist Number: {}".format(stat['change'])
         )
      else:
         view.erase_status(CHANGELIST_NUMBER_STATUS_KEY)

class SubforceSyncCommand(sublime_plugin.WindowCommand):
   def run(self, paths = []):
      if not paths:
         paths = [self.window.active_view().file_name()]

      dirtyOpenFiles = (view.file_name() for window in sublime.windows() for view in window.views() if view.is_dirty())

      dirtyFileInSyncPath = False
      for dirtyOpenFile in dirtyOpenFiles:
         for path in paths:
            if os.path.commonprefix([path, dirtyOpenFile]) == path:
               dirtyFileInSyncPath = True
               break

      performSync = not dirtyFileInSyncPath or \
         sublime.ok_cancel_dialog("You are about to sync over one or more files with unsaved modifications. Are you sure you want to proceed?")

      if performSync:
         for path in paths:
            if os.path.isdir(path):
               path = os.path.join(path, '...')

            # @TODO: Add a configurable logging system
            print("Subforce: syncing {}".format(path))
            p4.run_sync(path)

class SubforceAddCommand(sublime_plugin.WindowCommand):
   def run(self, paths = []):
      if not paths:
         paths = [self.window.active_view().file_name()]

      def onDoneCallback(selectedChangelistNumber):
         if selectedChangelistNumber:
            for path in paths:
               print("Subforce: adding {} to {}: ".format(path, selectedChangelistNumber))
               if selectedChangelistNumber == DEFAULT_CHANGELIST_NAME:
                  p4.run_add(selectedChangelistNumber, path)
               else:
                  p4.run_add("-c", selectedChangelistNumber, path)

      ChangelistManager(self.window).viewAllChangelists(self.window, onDoneCallback)

class SubforceCheckoutCommand(sublime_plugin.WindowCommand):
   def run(self, paths = []):
      if not paths:
         paths = [self.window.active_view().file_name()]

      def onDoneCallback(selectedChangelistNumber):
         if selectedChangelistNumber:
            for path in paths:
               if os.path.isdir(path):
                  path = os.path.join(path, '...')

               print("Subforce: checking out {} in {}: ".format(path, selectedChangelistNumber))
               if selectedChangelistNumber == DEFAULT_CHANGELIST_NAME:
                  p4.run_edit(path)
               else:
                  p4.run_edit("-c", selectedChangelistNumber, path)

      ChangelistManager(self.window).viewAllChangelists(self.window, onDoneCallback)

class SubforceRevertCommand(sublime_plugin.WindowCommand):
   def run(self, paths = []):
      if not paths:
         paths = [self.window.active_view().file_name()]

      for path in paths:
         print("Subforce: reverting {}".format(path))
         p4.run_revert(path)
         self._resetAutoCheckoutEventListenerSettingsForAllViews(path)

   def _resetAutoCheckoutEventListenerSettingsForAllViews(self, path):
      for view in getAllViewsForPath(path):
         AutoCheckoutEventListener.eraseAutoCheckoutEventListenerSettings(view)

class SubforceViewChangelistsCommand(sublime_plugin.WindowCommand):
   def run(self):
      ChangelistManager(self.window).viewAllChangelists(self.window, None)

class SubforceCreateChangelistCommand(sublime_plugin.WindowCommand):
   def run(self):
      ChangelistManager(self.window).createChangelist()

class SubforceEditChangelistCommand(sublime_plugin.WindowCommand):
   def run(self):
      changelistManager = ChangelistManager(self.window)

      def onDoneCallback(selectedChangelistNumber):
         if selectedChangelistNumber:
            print("Subforce: editing {}".format(selectedChangelistNumber))
            changelistManager.editChangelist(selectedChangelistNumber)

      changelistManager.viewAllChangelists(self.window, onDoneCallback)

class SubforceDeleteChangelistCommand(sublime_plugin.WindowCommand):
   def run(self):
      changelistManager = ChangelistManager(self.window)

      def onDoneCallback(selectedChangelistNumber):
         if selectedChangelistNumber:
            print("Subforce: deleting {}".format(selectedChangelistNumber))
            changelistManager.deleteChangelist(selectedChangelistNumber)

      changelistManager.viewAllChangelists(self.window, onDoneCallback)

class SubforceMoveToChangelistCommand(sublime_plugin.WindowCommand):
   def run(self, paths=[]):
      if not paths:
         paths = [self.window.active_view().file_name()]

      changelistManager = ChangelistManager(self.window)

      def onDoneCallback(selectedChangelistNumber):
         if selectedChangelistNumber:
            for path in paths:
               print("Subforce: moving {} to {}".format(path, selectedChangelistNumber))
               changelistManager.moveToChangelist(selectedChangelistNumber, path)

      changelistManager.viewAllChangelists(self.window, onDoneCallback)
