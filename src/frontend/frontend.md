# Adapy-viewer

A simple threejs (three-fiber to be precise) based viewer for Adapy.

It should be able to load any Adapy design or FE model and display it in the browser. 
The 3D models are sent as GLB files over a websocket connection along with instructions packaged in a json object.
This makes it possible to view the model in the browser without having to install any additional software. 

The viewer component should be sufficiently separated from the rest of the code so that it can be used in other 
projects. It should also be possible to 

## TODO

- [x] Send and receive GLB models over websocket
- [x] Add support for animations
  - [x] Add support for translations
  - [x] Add support for rotations
  - [x] Add support for deformations
- [x] Add a color legend for the simulation colorized models
- [x] Add websocket support for 2-way communication (receive GLB models and send back data-requests)
- [ ] Create a json object to hold both design and simulation over websocket in the same message
- [ ] Add a toggle to switch between design and simulation
- [ ] Add a REST api mode to serve the viewer as a standalone application
- [ ] Add a plotly based plotter for the simulation results.
  - [ ] Basic static support
  - [ ] Support animations using the same time slider as the 3D viewer.