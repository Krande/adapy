# Code_Aster

## Todo
The following is a summarized TODO list for fully functional support of Code Aster

- [x] Write MED files that can be opened in Salome Meca
- [x] Add Element and Node Set information to Code Aster input files (MED)
- [ ] Add section information (MED/COMM?)
- [ ] Create a valid export of Code_Aster analysis .med/.comm function

## Notes

* For element and node numbering to be consistent on roundtripping it is important that
node numbering is starting at 1 and has no gaps. Had to run `renumber()` method on the
  Nodes collection class prior to MED writing in order to successfully retrieve the same 
  numbering
  
## Further work

* It would be interesting to see if it is possible to define node numbering to something
other than starting at 1 and have gaps. Usually when defining artifical elements you would
  use a much higher number (at 10 000 100 000 or other to signify the type of element).
  
