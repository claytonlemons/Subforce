# Subforce
A Perforce plugin for Sublime Text 3 that leverages the P4Python API.

## Installation

To install, use Sublime's package manager [Package Control](https://packagecontrol.io).

Alternatively, you can manually clone this repository directly to your Sublime Text 3 Packages directory.

    $ cd path/to/Packages
    $ git clone https://github.com/MrElusive/Subforce.git

Note: you will also need to manually clone the [SublimeP4Python](https://github.com/MrElusive/SublimeP4Python) dependency.


## Settings

Refer to [Subforce.sublime-settings](https://github.com/MrElusive/Subforce/blob/master/Subforce.sublime-settings) for a description of the available settings.

Refer to the [Sublime's Settings documentation](https://www.sublimetext.com/docs/3/settings.html) and the [unofficial Settings documentation](http://docs.sublimetext.info/en/latest/customization/settings.html) for information on how to configure custom settings.

## Features

* Login - Log in to the Perforce server.
* Sync - retrieve the latest revision of one or more files or folders.
* Get Revision - retrieve a specific revision of a single file.
* Add - add one or more files or folders to a specified changelist (including a new changelist).
* Checkout - checkout one or more files or folders into a specified changelist (including a new changelist).
* Revert - revert one or more files or folders.
* Rename - rename a single file or folder.
* Move to Changelist - move one or more files or folders to a specified changelist (including a new changelist).
* View Timelapse - open the Time-lapse GUI for a single file.
* Resolve - resolve one or more files using the Resolve GUI.
* View Graphical Diff of Workspace - diff a single file against a depot revision using the P4Merge GUI.
* View Graphical Diff of Depot Revisions - diff two depot revisions of a single file using the P4Merge GUI.
* View Changelist - view all changelists for the current client
* Create Changelist - create a new changelist using the editor specified by the P4EDITOR environment variable or equivalent setting.
* Edit Changelist - edit a changelist using the editor specified by the P4EDITOR environment variable or equivalent setting.
* Delete Changelist - deletes a specified changelist if it contains no open files.
* Revert Files in Changelist - revert all open files in a specified changelist.
* Submit Changelist - Submit a changelist using the P4V GUI.
* Auto-Checkout-On-Save - checkout a single file into a specified changelist when saving.

## License

MIT License

Copyright (c) 2016 Clayton Lemons

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.