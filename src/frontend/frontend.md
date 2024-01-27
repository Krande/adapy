# Adapy-viewer

A simple threejs (three-fiber to be precise) based viewer for Adapy.

It should be able to load any Adapy design or analysis model and display it in the browser. 
The 3D models are sent as GLB files over a websocket connection.


## TODO

- [x] Send and receive GLB models over websocket
- [x] Add support for animations
  - [x] Add support for translations
  - [x] Add support for rotations
  - [x] Add support for deformations
- [ ] Add a color legend for the simulation colorized models
- [ ] Create a json object to hold both design and simulation over websocket in the same message
- [ ] Add a toggle to switch between design and simulation