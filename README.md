# election-administration
clerical tools

## Ballot Auditor 2026

This widget helps a local clerk verify that ballots marked as received in Michigan's "Qualified Voter File" system are actually physically present in the clerk's office.
The target audience is mid sized townships which do *not* have high speed tabulators, but *do* have enough mail-in ballots that they need some management in order to avoid creating extra work while scanning individual ballots through the tabulator.

The Ballot Auditor program accepts a CSV generated from QVF, sorts it by date received and ballot number,
then waits for Voter ID as user input - typically, from a hand held barcode scanner and the ballot envelope barcode.

If the barcode is in the list, the displayed record gets a green highlight.  If the barcode is NOT in the list (e.g. ballot is sorted into the wrong precinct box) then the program pops up a warning and sounds a notification.

At a (default) 50 found ballots, the user is prompted to bundle them.

User can click on a record to view full details.
