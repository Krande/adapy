from ada.core.guid import create_guid


def write_door(f, owner_history, insert_placement, insert_shape):
    return f.createIfcDoor(
        create_guid(),
        owner_history,
        "Door",
        "An awesome Door",
        None,
        insert_placement,
        insert_shape,
        None,
        None,
    )


def write_window(f, owner_history, insert_placement, insert_shape):
    return f.create_entity(
        "IfcWindow",
        create_guid(),
        owner_history,
        "Window",
        "An awesome window",
        None,
        insert_placement,
        insert_shape,
        None,
        None,
    )
