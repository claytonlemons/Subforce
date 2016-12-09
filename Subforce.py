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
import sys
import threading
import subprocess
import re
import tempfile
from .utilities import \
   getAllViewsForPath, \
   coercePathsToActiveViewIfNeeded, \
   getRevisionQualifiedDepotPath, \
   checkForAndGetSinglePath, \
   ellipsizeIfDirectory, \
   createRevision

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

CURRENT_WORKING_DIRECTORY_SETTING_KEY = 'current_working_directory'
DISPLAY_WARNINGS_SETTING_KEY = 'display_warnings'
USE_CONNECTION_INFO_SETTINGS_KEY = 'use_connection_info'
CONNECTION_INFO_PORT_SETTINGS_KEY = 'connection_info_port'
CONNECTION_INFO_USER_SETTINGS_KEY = 'connection_info_user'
CONNECTION_INFO_CLIENT_SETTINGS_KEY = 'connection_info_client'
DISABLE_AUTO_CHECKOUT_SETTINGS_KEY = 'disable_auto_checkout'

class SettingsWrapper(object):
   def __init__(self):
      self._settings = sublime.load_settings("Subforce.sublime-settings")

   def __getattr__(self, name):
      return getattr(self._settings, name)

   def getOrThrow(self, name):
      setting = self._settings.get(name)
      if setting is None:
         raise P4.P4Exception("Subforce: You must set the {} setting!".format(name))
      return setting

class PerforceWrapper(object):
   def __init__(self, squelchErrorAndWarninMessages=False):
      self._p4 = P4.P4()
      self._settings = SettingsWrapper()

      currentWorkingDirectorySetting = self._settings.get(CURRENT_WORKING_DIRECTORY_SETTING_KEY, None)
      projectPath = sublime.active_window().extract_variables()['folder']
      self._p4.cwd = currentWorkingDirectorySetting if currentWorkingDirectorySetting else projectPath

      self._p4.exception_level = 1 # Only errors are raised as exceptions. Warnings are accessed through p4.warnings

      self._p4.api_level = 79 # Lock to 2015.2 format

      self._contextManagerEnterLevel = 0
      self._squelchErrorAndWarninMessages = squelchErrorAndWarninMessages

   def __getattr__(self, name):
      attribute = getattr(self._p4, name)
      return attribute

   def __enter__(self):
      if self._contextManagerEnterLevel == 0:
         try:
            if self._settings.get(USE_CONNECTION_INFO_SETTINGS_KEY, False):
               self._p4.port = self._settings.getOrThrow(CONNECTION_INFO_PORT_SETTINGS_KEY)
               self._p4.user = self._settings.getOrThrow(CONNECTION_INFO_USER_SETTINGS_KEY)
               self._p4.client = self._settings.getOrThrow(CONNECTION_INFO_CLIENT_SETTINGS_KEY)

            self._p4.connect()

         except:
            if self.__exit__(*sys.exc_info()):
               pass
            else:
               raise

      self._contextManagerEnterLevel += 1
      return self

   def __exit__(self, type, value, traceback):
      noErrors = True

      if self._contextManagerEnterLevel == 1:
         self.handleWarnings()

         try:
            self._p4.disconnect()
         except P4.P4Exception:
            print("Subforce: failed to disconnect!")

         noErrors = self.handleErrors(type, value, traceback)

      self._contextManagerEnterLevel -= 1
      return noErrors

   def login(self, password):
      self._p4.password = password
      with self as p4:
         p4.run_login()
         print("Subforce: sucessfully logged in!")

   def handleWarnings(self):
      displayWarningsSetting = self._settings.get(DISPLAY_WARNINGS_SETTING_KEY, True)
      if not self._squelchErrorAndWarninMessages and displayWarningsSetting:
         for warning in self._p4.warnings:
            sublime.message_dialog(str(warning))


   def handleErrors(self, type, value, traceback):
      noErrors = True

      if type is P4.P4Exception:
         if not self._squelchErrorAndWarninMessages:
            sublime.error_message(str(value))
         noErrors = False
      elif type is not None:
         noErrors = False
      else:
         noErrors = True

      return noErrors

def plugin_loaded():
   print("Subforce: plugin loaded!")

def plugin_unloaded():
   print("Subforce: plugin unloaded!")

class SubforceDisplayDescriptionCommand(sublime_plugin.TextCommand):
   def run(self, edit, description = ""):
      # Enable editing momentarily to set description
      self.view.set_read_only(False)

      self.view.replace(edit, sublime.Region(0, self.view.size()), description)
      self.view.sel().clear()

      self.view.set_read_only(True)

class DescriptionOutputPanel(object):
   _outputPanelName = 'description_output_panel'
   _qualifiedOutputPanelName = 'output.description_output_panel'
   _outputPanelCreationLock = threading.Lock()

   def __init__(self, window):
      self._outputPanelCreationLock.acquire(blocking=True, timeout=1)
      self._window = window

      self._descriptionOutputPanel = self._window.find_output_panel(self._outputPanelName)
      if not self._descriptionOutputPanel:
         self._descriptionOutputPanel = self._window.create_output_panel(self._outputPanelName, True)
         self._descriptionOutputPanel.settings().set("is_description_output_panel", True)

      self._outputPanelCreationLock.release()

   def show(self, description):
      self._window.run_command(
         "show_panel",
         {
            "panel": self._qualifiedOutputPanelName
         }
      )

      self._descriptionOutputPanel.run_command(
         "subforce_display_description",
         {
            "description": description
         }
      )

   def hide(self):
      self._window.run_command(
         "hide_panel",
         {
            "panel": self._qualifiedOutputPanelName,
            "cancel": True
         }
      )

class ChangelistManager(object):

   def __init__(self, window, perforceWrapper):
      self._window = window
      self._perforceWrapper = perforceWrapper
      self._changelistDescriptionOutputPanel = DescriptionOutputPanel(self._window)

   def viewAllChangelists(self, onDoneCallback, includeNew=False, includeDefault=False):
      with self._perforceWrapper as p4:
         changelists = []

         if includeNew:
            changelists.append({"change": NEW_CHANGELIST_NAME, "desc": NEW_CHANGELIST_DESCRIPTION})

         if includeDefault:
            changelists.append({"change": DEFAULT_CHANGELIST_NAME, "desc": DEFAULT_CHANGELIST_DESCRIPTION})

         changelists.extend(p4.run_changes("-c", p4.client, "-s", "pending", "-l"))

         def onDone(selectedIndex):
            self._changelistDescriptionOutputPanel.hide()
            selectedChangelistNumber = changelists[selectedIndex]['change'] if selectedIndex >= 0 else None

            if selectedChangelistNumber == NEW_CHANGELIST_NAME:
               selectedChangelistNumber = self.createChangelist()

            if onDoneCallback and selectedChangelistNumber:
               onDoneCallback(selectedChangelistNumber)
            SubforceStatusUpdatingEventListener.updateStatus(self._window.active_view())

         def onHighlighted(selectedIndex):
            self._changelistDescriptionOutputPanel.show(changelists[selectedIndex]['desc'])

         changelistItems = [[changelist['change'], changelist['desc'][:250]] for changelist in changelists]

         self._window.show_quick_panel(
            changelistItems,
            onDone,
            sublime.KEEP_OPEN_ON_FOCUS_LOST,
            0,
            onHighlighted
         )

   def createChangelist(self):
      return self.editChangelist(None)

   def editChangelist(self, changelistNumber):
      with self._perforceWrapper as p4:
         if changelistNumber:
            changeResult = p4.run_change(changelistNumber)[0]
         else: # create a new changelist
            changeResult = p4.run_change()[0]

         changeResultRE = r'Change (\d+) (updated|created).'
         changeResultMatch = re.match(changeResultRE, changeResult)
         assert changeResultMatch and changeResultMatch.group(1).isdigit()

         return changeResultMatch.group(1)

   def deleteChangelist(self, changelistNumber):
      with self._perforceWrapper as p4:
         p4.run_change("-d", changelistNumber)

   def moveToChangelist(self, changelistNumber, file):
      with self._perforceWrapper as p4:
         p4.run_reopen("-c", changelistNumber, file)

   def checkoutInChangelist(self, changelistNumber, path):
      with self._perforceWrapper as p4:
         if changelistNumber == DEFAULT_CHANGELIST_NAME:
            p4.run_edit(path)
         else:
            p4.run_edit("-c", changelistNumber, path)

   def revertFilesInChangelist(self, changelistNumber):
      with self._perforceWrapper as p4:
         p4.run_revert("-c", changelistNumber, "//...")

   def addToChangelist(self, changelistNumber, file):
      with self._perforceWrapper as p4:
         if changelistNumber == DEFAULT_CHANGELIST_NAME:
            p4.run_add(changelistNumber, file)
         else:
            p4.run_add("-c", changelistNumber, file)


class SubforceAutoCheckoutEventListener(sublime_plugin.EventListener):
   def on_pre_save(self, view):
      if SettingsWrapper().get(DISABLE_AUTO_CHECKOUT_SETTINGS_KEY, False):
         return

      with PerforceWrapper() as p4:
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
         except:
            raise
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
         else:
            return

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
      settings = view.settings()
      try:
         with PerforceWrapper(squelchErrorAndWarninMessages=True) as p4:
            stat = p4.run_fstat(view.file_name())  # check if file is in depot
            if stat:
               stat = stat[0]
            else:
               return

            if "change" in stat:
               view.set_status(
                  CHANGELIST_NUMBER_STATUS_KEY,
                  "Changelist Number: {}".format(stat['change'])
               )
            else:
               view.erase_status(CHANGELIST_NUMBER_STATUS_KEY)
      except P4.P4Exception: # Squelch all Perforce exceptions
         pass

class SubforceLoginCommand(sublime_plugin.WindowCommand):
   savedPasswordCharacters = []

   def run(self):
      def onDone(password):
         PerforceWrapper().login("".join(self.savedPasswordCharacters))

      def onChange(password):
         nextPasswordCharacter = password[len(self.savedPasswordCharacters):]
         if len(password) < len(self.savedPasswordCharacters):
            self.savedPasswordCharacters.pop()
         elif len(password) > len(self.savedPasswordCharacters):
            self.savedPasswordCharacters.append(nextPasswordCharacter)
         else:
            return

         hiddenPassword = '*' * len(password)

         self.window.show_input_panel(
            "Password",
            hiddenPassword,
            onDone,
            onChange,
            None
         )

      self.window.show_input_panel(
         "Password",
         "",
         onDone,
         onChange,
         None
      )


class SubforceSyncCommand(sublime_plugin.WindowCommand):
   def run(self, paths = []):
      with PerforceWrapper() as p4:
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

         paths = [ellipsizeIfDirectory(path) for path in paths]

         if performSync:
            # @TODO: Add a configurable logging system
            print("Subforce: syncing\n\t{}".format("\n\t".join(paths)))
            p4.run_sync(paths)

class SubforceAddCommand(sublime_plugin.WindowCommand):
   def run(self, paths = []):
      perforceWrapper = PerforceWrapper()
      changelistManager = ChangelistManager(self.window, perforceWrapper)

      paths = coercePathsToActiveViewIfNeeded(paths, self.window)
      paths = [ellipsizeIfDirectory(path) for path in paths]

      def onDoneCallback(selectedChangelistNumber):
         print("Subforce: adding\n\t{}\nto changelist {}: ".format("\n\t".join(paths), selectedChangelistNumber))
         changelistManager.addToChangelist(selectedChangelistNumber, paths)

      changelistManager.viewAllChangelists(onDoneCallback, includeNew=True, includeDefault=True)

class SubforceGetRevisionCommand(sublime_plugin.WindowCommand):
   def run(self, paths):
      perforceWrapper = PerforceWrapper()
      revisionManager = RevisionManager(self.window, perforceWrapper)

      with perforceWrapper as p4:
         paths = coercePathsToActiveViewIfNeeded(paths, self.window)
         path = checkForAndGetSinglePath(paths)
         if not path:
            return
         path = ellipsizeIfDirectory(path)

         def onDoneCallback(selectedRevision):
            revisionManager.getRevision(selectedRevision, path)

         revisionManager.showHaveHeadAndFileRevisions(path, onDoneCallback)


class SubforceCheckoutCommand(sublime_plugin.WindowCommand):
   def run(self, paths = []):
      perforceWrapper = PerforceWrapper()
      changelistManager = ChangelistManager(self.window, perforceWrapper)

      paths = coercePathsToActiveViewIfNeeded(paths, self.window)
      paths = [ellipsizeIfDirectory(path) for path in paths]

      def onDoneCallback(selectedChangelistNumber):
         print("Subforce: checking out\n\t{}\nin changelist {}: ".format("\n\t".join(paths), selectedChangelistNumber))
         changelistManager.checkoutInChangelist(selectedChangelistNumber, paths)

      changelistManager.viewAllChangelists(onDoneCallback, includeNew=True, includeDefault=True)

class SubforceRevertCommand(sublime_plugin.WindowCommand):
   def run(self, paths = []):
      with PerforceWrapper() as p4:
         paths = coercePathsToActiveViewIfNeeded(paths, self.window)
         ellipsizedPaths = [ellipsizeIfDirectory(path) for path in paths]

         print("Subforce: reverting\n\t{}".format("\n\t".join(ellipsizedPaths)))
         p4.run_revert(ellipsizedPaths)

         self._resetAutoCheckoutEventListenerSettingsForAllViews(paths)

   def _resetAutoCheckoutEventListenerSettingsForAllViews(self, paths):
      for path in paths:
         for view in getAllViewsForPath(path):
            SubforceAutoCheckoutEventListener.eraseAutoCheckoutEventListenerSettings(view)

class SubforceRenameCommand(sublime_plugin.WindowCommand):
   def run(self, paths = []):
      perforceWrapper = PerforceWrapper()
      changelistManager = ChangelistManager(self.window, perforceWrapper)

      with perforceWrapper as p4:
         paths = coercePathsToActiveViewIfNeeded(paths, self.window)
         path = checkForAndGetSinglePath(paths)
         if not path:
            return
         path = ellipsizeIfDirectory(path)

         stat = p4.run_fstat(path)
         if 'action' not in stat[0]:
            requiresCheckout = True
         else:
            requiresCheckout = False

         if requiresCheckout and not \
               sublime.ok_cancel_dialog(
                  "File must be checked out before it can be renamed. Do you want to check it out now?",
                  "Checkout"
               ):
            return

         def renameFile(file):
            def onDoneRenameCallback(newFileName):
               with perforceWrapper as p4: # necessary because the callback runs in a different thread
                  p4.run_rename(file, newFileName)

            self.window.show_input_panel(
               "New File Name",
               file,
               onDoneRenameCallback,
               None,
               None
            )

         if requiresCheckout:
            def onDoneViewingChangelistsCallback(selectedChangelistNumber):
               changelistManager.checkoutInChangelist(selectedChangelistNumber, path)
               renameFile(path)
            changelistManager.viewAllChangelists(onDoneViewingChangelistsCallback, includeNew=True, includeDefault=True)
         else:
            renameFile(path)

class SubforceViewChangelistsCommand(sublime_plugin.WindowCommand):
   def run(self):
      perforceWrapper = PerforceWrapper()
      ChangelistManager(self.window, perforceWrapper).viewAllChangelists(None)

class SubforceCreateChangelistCommand(sublime_plugin.WindowCommand):
   def run(self):
      perforceWrapper = PerforceWrapper()
      ChangelistManager(self.window, perforceWrapper).createChangelist()

class SubforceEditChangelistCommand(sublime_plugin.WindowCommand):
   def run(self):
      perforceWrapper = PerforceWrapper()
      changelistManager = ChangelistManager(self.window, perforceWrapper)

      def onDoneCallback(selectedChangelistNumber):
            print("Subforce: editing {}".format(selectedChangelistNumber))
            changelistManager.editChangelist(selectedChangelistNumber)

      changelistManager.viewAllChangelists(onDoneCallback)

class SubforceDeleteChangelistCommand(sublime_plugin.WindowCommand):
   def run(self):
      perforceWrapper = PerforceWrapper()
      changelistManager = ChangelistManager(self.window, perforceWrapper)

      def onDoneCallback(selectedChangelistNumber):
            print("Subforce: deleting {}".format(selectedChangelistNumber))
            changelistManager.deleteChangelist(selectedChangelistNumber)

      changelistManager.viewAllChangelists(onDoneCallback)

class SubforceMoveToChangelistCommand(sublime_plugin.WindowCommand):
   def run(self, paths=[]):
      perforceWrapper = PerforceWrapper()
      changelistManager = ChangelistManager(self.window, perforceWrapper)

      paths = coercePathsToActiveViewIfNeeded(paths, self.window)
      paths = [ellipsizeIfDirectory(path) for path in paths]

      def onDoneCallback(selectedChangelistNumber):
         print("Subforce: moving\n\t{}\nto changelist {}".format("\n\t".join(paths), selectedChangelistNumber))
         changelistManager.moveToChangelist(selectedChangelistNumber, paths)

      changelistManager.viewAllChangelists(onDoneCallback, includeNew=True, includeDefault=True)

class SubforceRevertFilesInChangelistCommand(sublime_plugin.WindowCommand):
   def run(self):
      perforceWrapper = PerforceWrapper()
      changelistManager = ChangelistManager(self.window, perforceWrapper)

      def onDoneCallback(selectedChangelistNumber):
         print("Subforce: reverting files in {}".format(selectedChangelistNumber))
         changelistManager.revertFilesInChangelist(selectedChangelistNumber)

      changelistManager.viewAllChangelists(onDoneCallback)

def executeP4VCCommand(command, *args):
   with PerforceWrapper() as p4:
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
      perforceWrapper = PerforceWrapper()
      changelistManager = ChangelistManager(self.window, perforceWrapper)

      def onDoneCallback(selectedChangelistNumber):
         if selectedChangelistNumber:
            executeP4VCCommand("submit", "-c", selectedChangelistNumber)

      changelistManager.viewAllChangelists(onDoneCallback)

class SubforceResolveCommand(sublime_plugin.WindowCommand):
   def run(self, paths=[]):
      paths = coercePathsToActiveViewIfNeeded(paths, self.window)

      executeP4VCCommand("resolve", " ".join(paths))

class RevisionManager:
   def __init__(self, window, perforceWrapper):
      self._window = window
      self._perforceWrapper = perforceWrapper
      self._revisionDescriptionOutputPanel = DescriptionOutputPanel(self._window)
      self._callbackDepth = 0

   def diffClientFileAgainstDepotRevision(self, revision, file):
      with self._perforceWrapper as p4:
         depotFilePath = p4.run_fstat(file)[0]['depotFile']

         temporaryDepotFilePath = self._createTemporaryDepotFile(depotFilePath, revision)
         self._startP4MergeThread(
            temporaryDepotFilePath,
            file,
            getRevisionQualifiedDepotPath(depotFilePath, revision),
            "{} (workspace file)".format(file)
         )

   def diffDepotRevisions(self, revision1, revision2, file):
      with self._perforceWrapper as p4:
         (revision1, revision2) = sorted([revision1, revision2]) # ensures the most recent revision is on the right

         depotFilePath = p4.run_fstat(file)[0]['depotFile']

         temporaryDepotFilePath1 = self._createTemporaryDepotFile(depotFilePath, revision1)
         temporaryDepotFilePath2 = self._createTemporaryDepotFile(depotFilePath, revision2)
         self._startP4MergeThread(
            temporaryDepotFilePath1,
            temporaryDepotFilePath2,
            getRevisionQualifiedDepotPath(depotFilePath, revision1),
            getRevisionQualifiedDepotPath(depotFilePath, revision2)
         )

   def showHaveHeadRevisions(self, onDoneCallback):
      revisions = [{'revision': HAVE_REVISION_NAME, 'desc': HAVE_REVISION_DESCRIPTION}, {'revision': HEAD_REVISION_NAME, 'desc': HEAD_REVISION_DESCRIPTION}]
      self._showRevisions(revisions, onDoneCallback)

   def showHaveHeadAndFileRevisions(self, file, onDoneCallback):
      with self._perforceWrapper as p4:
         revisions = [createRevision(HAVE_REVISION_NAME, HAVE_REVISION_DESCRIPTION), createRevision(HEAD_REVISION_NAME, HEAD_REVISION_DESCRIPTION)]
         revisions.extend(
            [
               createRevision(str(revision.rev), revision.desc)
               for revision in p4.run_filelog("-l", file)[0].revisions
            ]
         )
         self._showRevisions(revisions, onDoneCallback)

   def getRevision(self, revision, file):
      with self._perforceWrapper as p4:
         depotFilePath = p4.run_fstat(file)[0]['depotFile']
         p4.run_sync(getRevisionQualifiedDepotPath(depotFilePath, revision))

   def _showRevisions(self, revisions, onDoneCallback):
      self._callbackDepth += 1
      def onDone(selectedIndex):
         selectedRevision = revisions[selectedIndex]['revision'] if selectedIndex >= 0 else None

         if onDoneCallback and selectedRevision:
            onDoneCallback(selectedRevision)

         if self._callbackDepth == 1: # last one out turns off the lights.
            self._revisionDescriptionOutputPanel.hide()
         self._callbackDepth -= 1

      def onHighlighted(selectedIndex):
         self._revisionDescriptionOutputPanel.show(revisions[selectedIndex]['desc'])

      revisionItems = [[revision['revision'], revision['desc'][:250]] for revision in revisions]

      self._window.show_quick_panel(
         revisionItems,
         onDone,
         sublime.KEEP_OPEN_ON_FOCUS_LOST,
         0,
         onHighlighted
      )

   def _startP4MergeThread(self, leftFile, rightFile, leftFileAlias, rightFileAlias):
      def target():
         command = ["p4merge.exe", '-nl', leftFileAlias, '-nr', rightFileAlias, leftFile, rightFile]
         process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
         stdout, stderr = process.communicate()
         if stdout:
            print(stdout)
         if stderr:
            print(stderr)

      threading.Thread(target=target).start()

   def _createTemporaryDepotFile(self, file, revision):
      with self._perforceWrapper as p4:

         # @TODO: At some point in time, we may want to create temporary files with the same naming convention as p4v.
         with tempfile.NamedTemporaryFile(prefix="subforce_", delete=False) as temporaryFile:
            depotFilePath = getRevisionQualifiedDepotPath(file, revision)
            depotFileText = p4.run_print(depotFilePath)[1]
            temporaryFile.write(bytes(depotFileText, 'UTF-8'))
            return temporaryFile.name



class SubforceViewGraphicalDiffWorkspaceFileCommand(sublime_plugin.WindowCommand):
   '''
   Diffs one or more files against a depot revision.
   A single file may be diffed against any revision.
   Multiple files may only be diffed against the have or head revisions.
   '''
   def run(self, paths=[]):
      perforceWrapper = PerforceWrapper()
      revisionManager = RevisionManager(self.window, perforceWrapper)

      paths = coercePathsToActiveViewIfNeeded(paths, self.window)

      if len(paths) == 1:
         path = paths[0]

         def onDoneCallback(selectedRevision):
            revisionManager.diffClientFileAgainstDepotRevision(selectedRevision, path)

         revisionManager.showHaveHeadAndFileRevisions(path, onDoneCallback)
      else:
         def onDoneCallback(selectedRevision):
            for path in paths:
               revisionManager.diffClientFileAgainstDepotRevision(selectedRevision, path)

         revisionManager.showHaveHeadRevisions(onDoneCallback)

class SubforceViewGraphicalDiffDepotRevisionsCommand(sublime_plugin.WindowCommand):
   '''
   Diffs two depot revisions of a given file.
   Only a single file may be diffed at a time.
   '''
   def run(self, paths=[]):
      perforceWrapper = PerforceWrapper()
      revisionManager = RevisionManager(self.window, perforceWrapper)

      paths = coercePathsToActiveViewIfNeeded(paths, self.window)

      path = checkForAndGetSinglePath(paths)
      if not path:
         return

      def onDoneCallback1(selectedRevision1):
         def onDoneCallback2(selectedRevision2):
            revisionManager.diffDepotRevisions(selectedRevision1, selectedRevision2, path)
         revisionManager.showHaveHeadAndFileRevisions(path, onDoneCallback2)
      revisionManager.showHaveHeadAndFileRevisions(path, onDoneCallback1)

