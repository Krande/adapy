"""
Events
------

A simple example to demonstrate events.
"""

from rendercanvas.auto import RenderCanvas, loop


canvas = RenderCanvas(title="RenderCanvas events")


@canvas.add_event_handler("*")
def process_event(event):
    if event["event_type"] not in ["pointer_move", "before_draw", "draw"]:
        print(event)


if __name__ == "__main__":
    loop.run()
