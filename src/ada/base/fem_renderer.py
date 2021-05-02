from pythreejs import Group


class FemRenderer:
    def __init__(self):
        # the group of 3d and 2d objects to render
        self._displayed_pickable_objects = Group()

    def Display(self, position=None, rotation=None):
        # Get the overall bounding box
        if self._shapes:
            self._bb = BoundingBox([self._shapes.values()])
        else:  # if nothing registered yet, create a fake bb
            self._bb = BoundingBox([[BRepPrimAPI_MakeSphere(5.).Shape()]])
        bb_max = self._bb.max
        orbit_radius = 1.5 * self._bb._max_dist_from_center()

        # Set up camera
        camera_target = self._bb.center
        camera_position = _add(self._bb.center,
                               self._scale([1, 1, 1] if position is None else self._scale(position)))
        camera_zoom = self._camera_initial_zoom

        self._camera = CombinedCamera(position=camera_position,
                                      width=self._size[0], height=self._size[1])
        self._camera.up = (0.0, 0.0, 1.0)
        self._camera.mode = 'orthographic'
        self._camera_target = camera_target
        self._camera.position = camera_position
        if rotation is not None:
            self._camera.rotation = rotation
        # Set up lights in every of the 8 corners of the global bounding box
        positions = list(itertools.product(*[(-orbit_radius, orbit_radius)] * 3))
        key_lights = [DirectionalLight(color='white',
                                       position=pos,
                                       intensity=0.5) for pos in positions]
        ambient_light = AmbientLight(intensity=0.1)

        # Set up Helpers
        self.axes = Axes(bb_center=self._bb.center, length=bb_max * 1.1)
        self.horizontal_grid = Grid(bb_center=self._bb.center, maximum=bb_max,
                                    colorCenterLine='#aaa', colorGrid='#ddd')
        self.vertical_grid = Grid(bb_center=self._bb.center, maximum=bb_max,
                                  colorCenterLine='#aaa', colorGrid='#ddd')
        # Set up scene
        environment = self.axes.axes + key_lights + [ambient_light,
                                                     self.horizontal_grid.grid,
                                                     self.vertical_grid.grid,
                                                     self._camera]

        scene_shp = Scene(children=[self._displayed_pickable_objects,
                                    self._displayed_non_pickable_objects] + environment)

        # Set up Controllers
        self._controller = OrbitControls(controlling=self._camera,
                                         target=camera_target,
                                         target0=camera_target)
        # Update controller to instantiate camera position
        self._camera.zoom = camera_zoom
        self._update()

        # setup Picker
        self._picker = Picker(controlling=self._displayed_pickable_objects, event='dblclick')
        self._picker.observe(self.click)

        self._renderer = Renderer(camera=self._camera,
                                  background=self._background,
                                  background_opacity=self._background_opacity,
                                  scene=scene_shp,
                                  controls=[self._controller, self._picker],
                                  width=self._size[0],
                                  height=self._size[1],
                                  antialias=True)

        # set rotation and position for each grid
        self.horizontal_grid.set_position((0, 0, 0))
        self.horizontal_grid.set_rotation((math.pi / 2.0, 0, 0, "XYZ"))

        self.vertical_grid.set_position((0, - bb_max, 0))

        self._savestate = (self._camera.rotation, self._controller.target)

        # then display both 3d widgets and webui
        display(HBox([VBox([HBox(self._controls), self._renderer]),
                      self.html]))