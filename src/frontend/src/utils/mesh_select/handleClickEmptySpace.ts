import {deselectObject} from "./deselectObject";

export function handleClickEmptySpace(event: MouseEvent) {
    event.stopPropagation();
    deselectObject();
}