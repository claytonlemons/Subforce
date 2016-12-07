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
import subprocess
import re
import tempfile
from .utilities import getAllViewsForPath, coercePathsToActiveViewIfNeeded, getRevisionQualifiedDepotPath

NEW_CHANGELIST_NAME = "new"
NEW_CHANGELIST_DESCRIPTION = "Creates a new changelist."
DEFAULT_CHANGELIST_NAME = "default"
DEFAULT_CHANGELIST_DESCRIPTION = "The default changelist."

HAVE_REVISION_NAME = "have"
HAVE_REVISION_DESCRIPTION = "The currently synced revision."
HEAD_REVISION_NAME = "head"
HEAD_REVISION_DESCRIPTION = "The most recently checked-in revision."

FILE_CHECKED_OUT_SETTING_KEY = "subforce_file_checked_out"
FILE_NOT_IN_DEPOT_SETTING_KEY = "subforce_file_not_in_depot"
CHANGELIST_NUMBER_STATUS_KEY = "subforce_changelist_number"

p4 = None

createRevision = lambda revision, description: {'revision': revision, 'desc': description}

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
            "cancel": True
         }
      )


class ChangelistManager(object):

   def __init__(self, window):
      self.window = window
      self.changelistDescriptionOutputPanel = ChangelistDescriptionOutputPanel(self.window)

   def viewAllChangelists(self, onDoneCallback, includeNew=False, includeDefault=False):
      changelists = []

      if includeNew:
         changelists.append({"change": NEW_CHANGELIST_NAME, "desc": NEW_CHANGELIST_DESCRIPTION})

      if includeDefault:
         changelists.append({"change": DEFAULT_CHANGELIST_NAME, "desc": DEFAULT_CHANGELIST_DESCRIPTION})

      changelists.extend(p4.run("changes", "-c", p4.client, "-s", "pending", "-l"))

      def onDone(selectedIndex):
         print("Selected: {}".format(selectedIndex))
         self.changelistDescriptionOutputPanel.hide()
         selectedChangelistNumber = changelists[selectedIndex]['change'] if selectedIndex >= 0 else None

         if selectedChangelistNumber == NEW_CHANGELIST_NAME:
            selectedChangelistNumber = self.createChangelist()

         if onDoneCallback and selectedChangelistNumber:
            onDoneCallback(selectedChangelistNumber)
         SubforceStatusUpdatingEventListener.updateStatus(self.window.active_view())

      def onHighlighted(selectedIndex):
         self.changelistDescriptionOutputPanel.show(changelists[selectedIndex]['desc'])

      changelistItems = [[changelist['change'], changelist['desc'][:250]] for changelist in changelists]

      self.window.show_quick_panel(
         changelistItems,
         onDone,
         sublime.KEEP_OPEN_ON_FOCUS_LOST,
         0,
         onHighlighted
      )

   def createChangelist(self):
      return self.editChangelist(None)

   def editChangelist(self, changelistNumber):
      if changelistNumber:
         changeResult = p4.run_change(changelistNumber)[0]
      else: # create a new changelist
         changeResult = p4.run_change()[0]

      changeResultRE = r'Change (\d+) (updated|created).'
      changeResultMatch = re.match(changeResultRE, changeResult)
      assert changeResultMatch and changeResultMatch.group(1).isdigit()

      return changeResultMatch.group(1)

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

      moveToChangelist = sublime.ok_cancel_dialog(
         "You're file has been checked out in the default changelist. Do you want to move it to another changelist?",
         "Move"
      )

      if moveToChangelist:
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
      paths = coercePathsToActiveViewIfNeeded(paths, self.window)

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
      paths = coercePathsToActiveViewIfNeeded(paths, self.window)

      def onDoneCallback(selectedChangelistNumber):
         for path in paths:
            print("Subforce: adding {} to {}: ".format(path, selectedChangelistNumber))
            if selectedChangelistNumber == DEFAULT_CHANGELIST_NAME:
               p4.run_add(selectedChangelistNumber, path)
            else:
               p4.run_add("-c", selectedChangelistNumber, path)

      ChangelistManager(self.window).viewAllChangelists(onDoneCallback)

class SubforceCheckoutCommand(sublime_plugin.WindowCommand):
   def run(self, paths = []):
      paths = coercePathsToActiveViewIfNeeded(paths, self.window)

      def onDoneCallback(selectedChangelistNumber):
         for path in paths:
            if os.path.isdir(path):
               path = os.path.join(path, '...')

            print("Subforce: checking out {} in {}: ".format(path, selectedChangelistNumber))
            if selectedChangelistNumber == DEFAULT_CHANGELIST_NAME:
               p4.run_edit(path)
            else:
               p4.run_edit("-c", selectedChangelistNumber, path)

      ChangelistManager(self.window).viewAllChangelists(onDoneCallback, includeNew=True, includeDefault=True)

class SubforceRevertCommand(sublime_plugin.WindowCommand):
   def run(self, paths = []):
      paths = coercePathsToActiveViewIfNeeded(paths, self.window)

      for path in paths:
         print("Subforce: reverting {}".format(path))
         p4.run_revert(path)
         self._resetAutoCheckoutEventListenerSettingsForAllViews(path)

   def _resetAutoCheckoutEventListenerSettingsForAllViews(self, path):
      for view in getAllViewsForPath(path):
         SubforceAutoCheckoutEventListener.eraseAutoCheckoutEventListenerSettings(view)

class SubforceViewChangelistsCommand(sublime_plugin.WindowCommand):
   def run(self):
      ChangelistManager(self.window).viewAllChangelists(None)

class SubforceCreateChangelistCommand(sublime_plugin.WindowCommand):
   def run(self):
      ChangelistManager(self.window).createChangelist()

class SubforceEditChangelistCommand(sublime_plugin.WindowCommand):
   def run(self):
      changelistManager = ChangelistManager(self.window)

      def onDoneCallback(selectedChangelistNumber):
         print("Subforce: editing {}".format(selectedChangelistNumber))
         changelistManager.editChangelist(selectedChangelistNumber)

      changelistManager.viewAllChangelists(onDoneCallback)

class SubforceDeleteChangelistCommand(sublime_plugin.WindowCommand):
   def run(self):
      changelistManager = ChangelistManager(self.window)

      def onDoneCallback(selectedChangelistNumber):
         print("Subforce: deleting {}".format(selectedChangelistNumber))
         changelistManager.deleteChangelist(selectedChangelistNumber)

      changelistManager.viewAllChangelists(onDoneCallback)

class SubforceMoveToChangelistCommand(sublime_plugin.WindowCommand):
   def run(self, paths=[]):
      paths = coercePathsToActiveViewIfNeeded(paths, self.window)

      changelistManager = ChangelistManager(self.window)

      def onDoneCallback(selectedChangelistNumber):
         for path in paths:
            print("Subforce: moving {} to {}".format(path, selectedChangelistNumber))
            changelistManager.moveToChangelist(selectedChangelistNumber, path)

      changelistManager.viewAllChangelists(onDoneCallback, includeNew=True, includeDefault=True)


def executeP4VCCommand(command, *args):
   command = " ".join(["p4vc.exe", command] + list(args))
   print("Subforce: executing p4vc command '{}'".format(command))
   process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True, cwd=p4.cwd)
   stdout, stderr = process.communicate()
   if stdout:
      print(stdout)
   if stderr:
      print(stderr)

class SubforceViewTimelapseCommand(sublime_plugin.WindowCommand):
   def run(self, paths=[]):
      paths = coercePathsToActiveViewIfNeeded(paths, self.window)

      for path in paths:
         executeP4VCCommand("timelapseview", path)

class SubforceSubmitChangelistCommand(sublime_plugin.WindowCommand):
   def run(self):
      changelistManager = ChangelistManager(self.window)

      def onDoneCallback(selectedChangelistNumber):
         if selectedChangelistNumber:
            executeP4VCCommand("submit", "-c", selectedChangelistNumber)

      changelistManager.viewAllChangelists(onDoneCallback)

class SubforceResolveCommand(sublime_plugin.WindowCommand):
   def run(self, paths=[]):
      paths = coercePathsToActiveViewIfNeeded(paths, self.window)

      executeP4VCCommand("resolve", " ".join(paths))


class GraphicalDiffManager:
   def __init__(self, window):
      self.window = window
      self.changelistDescriptionOutputPanel = ChangelistDescriptionOutputPanel(self.window)
      self._callbackDepth = 0


   def diffClientFileAgainstDepotRevision(self, file, revision):
      depotFilePath = p4.run_fstat(file)[0]['depotFile']

      temporaryDepotFilePath = self._createTemporaryDepotFile(depotFilePath, revision)
      self._startP4MergeThread(
         temporaryDepotFilePath,
         file,
         getRevisionQualifiedDepotPath(depotFilePath, revision),
         "{} (workspace file)".format(file)
      )

   def diffDepotRevisions(self, file, revision1, revision2):
      (revision1, revision2) = (revision1, revision2) if int(revision1) < int(revision2) else (revision2, revision1)
      depotFilePath = p4.run_fstat(file)[0]['depotFile']

      temporaryDepotFilePath1 = self._createTemporaryDepotFile(depotFilePath, revision1)
      temporaryDepotFilePath2 = self._createTemporaryDepotFile(depotFilePath, revision2)
      self._startP4MergeThread(
         temporaryDepotFilePath1,
         temporaryDepotFilePath2,
         getRevisionQualifiedDepotPath(depotFilePath, revision1),
         getRevisionQualifiedDepotPath(depotFilePath, revision2)
      )

   def _startP4MergeThread(self, leftFile, rightFile, leftFileAlias, rightFileAlias):
      def target():
         command = ["p4merge.exe", '-nl', leftFileAlias, '-nr', rightFileAlias, leftFile, rightFile]
         process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
         print("hello")
         stdout, stderr = process.communicate()
         if stdout:
            print(stdout)
         if stderr:
            print(stderr)

      threading.Thread(target=target).start()

   def _createTemporaryDepotFile(self, file, revision):
      # @TODO: At some point in time, we may want to create temporary files with the same naming convention as p4v.
      with tempfile.NamedTemporaryFile(prefix="subforce_", delete=False) as temporaryFile:
         depotFilePath = getRevisionQualifiedDepotPath(file, revision)
         depotFileText = p4.run_print(depotFilePath)[1]
         temporaryFile.write(bytes(depotFileText, 'UTF-8'))
         return temporaryFile.name


   def showHaveHeadRevisions(self, onDoneCallback):
      revisions = [{'revision': HAVE_REVISION_NAME, 'desc': HAVE_REVISION_DESCRIPTION}, {'revision': HEAD_REVISION_NAME, 'desc': HEAD_REVISION_DESCRIPTION}]
      self._showRevisions(revisions, onDoneCallback)


   def showHaveHeadAndFileRevisions(self, file, onDoneCallback):
      revisions = [createRevision(HAVE_REVISION_NAME, HAVE_REVISION_DESCRIPTION), createRevision(HEAD_REVISION_NAME, HEAD_REVISION_DESCRIPTION)]
      revisions.extend(
         [
            createRevision(str(revision.rev), revision.desc)
            for revision in p4.run_filelog("-l", file)[0].revisions
         ]
      )
      self._showRevisions(revisions, onDoneCallback)

   def _showRevisions(self, revisions, onDoneCallback):
      self._callbackDepth += 1
      def onDone(selectedIndex):
         selectedRevision = revisions[selectedIndex]['revision'] if selectedIndex >= 0 else None

         if onDoneCallback and selectedRevision:
            onDoneCallback(selectedRevision)

         if self._callbackDepth == 1: # last one out turns off the lights.
            self.changelistDescriptionOutputPanel.hide()
         self._callbackDepth -= 1

      def onHighlighted(selectedIndex):
         self.changelistDescriptionOutputPanel.show(revisions[selectedIndex]['desc'])

      revisionItems = [[revision['revision'], revision['desc'][:250]] for revision in revisions]

      self.window.show_quick_panel(
         revisionItems,
         onDone,
         sublime.KEEP_OPEN_ON_FOCUS_LOST,
         0,
         onHighlighted
      )

class SubforceViewGraphicalDiffWorkspaceFileCommand(sublime_plugin.WindowCommand):
   '''
   Diffs one or more files against a depot revision.
   A single file may be diffed against any revision.
   Multiple files may only be diffed against the have or head revisions.
   '''
   def run(self, paths=[]):
      paths = coercePathsToActiveViewIfNeeded(paths, self.window)

      graphicalDiffManager = GraphicalDiffManager(self.window)

      if len(paths) == 1:
         path = paths[0]

         def onDoneCallback(selectedRevision):
            graphicalDiffManager.diffClientFileAgainstDepotRevision(path, selectedRevision)

         graphicalDiffManager.showHaveHeadAndFileRevisions(path, onDoneCallback)
      else:
         def onDoneCallback(selectedRevision):
            for path in paths:
               graphicalDiffManager.diffClientFileAgainstDepotRevision(path, selectedRevision)

         graphicalDiffManager.showHaveHeadRevisions(onDoneCallback)

class SubforceViewGraphicalDiffDepotRevisionsCommand(sublime_plugin.WindowCommand):
   '''
   Diffs two depot revisions of a given file.
   Only a single file may be diffed at a time.
   '''
   def run(self, paths=[]):
      paths = coercePathsToActiveViewIfNeeded(paths, self.window)

      graphicalDiffManager = GraphicalDiffManager(self.window)

      if len(paths) > 1:
         sublime.error_message("A graphical diff of depot revisions can only be performed on one workspace file at a time.")
         return
      else:
         path = paths[0]

         def onDoneCallback1(selectedRevision1):
            def onDoneCallback2(selectedRevision2):
               graphicalDiffManager.diffDepotRevisions(path, selectedRevision1, selectedRevision2)
            print(selectedRevision1)
            graphicalDiffManager.showHaveHeadAndFileRevisions(path, onDoneCallback2)
         graphicalDiffManager.showHaveHeadAndFileRevisions(path, onDoneCallback1)

