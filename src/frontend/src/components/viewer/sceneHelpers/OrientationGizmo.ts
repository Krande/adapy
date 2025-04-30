//https://raw.githubusercontent.com/jrj2211/three-orientation-gizmo/master/src/OrientationGizmo.js
/*
MIT License

Copyright (c) 2020 jrj2211

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
*/

// eslint-disable-next-line @typescript-eslint/ban-ts-comment
// @ts-nocheck
import { Matrix4, Vector3 } from "three";

export class OrientationGizmo extends HTMLElement {
    constructor(camera, options) {
        super();
        this.camera = camera;
        this.options = Object.assign(
            {
                size: 150,
                padding: 8,
                bubbleSizePrimary: 10,
                bubbleSizeSeconday: 10,
                showSecondary: true,
                lineWidth: 2,
                fontSize: "10px",
                fontFamily: "arial",
                fontWeight: "bold",
                fontColor: "#151515",
                fontYAdjust: 0,
                colors: {
                    x: ["#f73c3c", "#942424"],
                    y: ["#6ccb26", "#417a17"],
                    z: ["#178cf0", "#0e5490"]
                }
            },
            options
        );

        // Function called when axis is clicked
        this.onAxisSelected = null;

        // Generate list of axes
        this.bubbles = [
            {
                axis: "x",
                direction: new Vector3(1, 0, 0),
                size: this.options.bubbleSizePrimary,
                color: this.options.colors.x,
                line: this.options.lineWidth,
                label: "X"
            },
            {
                axis: "y",
                direction: new Vector3(0, 1, 0),
                size: this.options.bubbleSizePrimary,
                color: this.options.colors.y,
                line: this.options.lineWidth,
                label: "Y"
            },
            {
                axis: "z",
                direction: new Vector3(0, 0, 1),
                size: this.options.bubbleSizePrimary,
                color: this.options.colors.z,
                line: this.options.lineWidth,
                label: "Z"
            },
            {
                axis: "-x",
                direction: new Vector3(-1, 0, 0),
                size: this.options.bubbleSizeSeconday,
                color: this.options.colors.x,
                label: "-X"
            },
            {
                axis: "-y",
                direction: new Vector3(0, -1, 0),
                size: this.options.bubbleSizeSeconday,
                color: this.options.colors.y,
                label: "-Y"
            },
            {
                axis: "-z",
                direction: new Vector3(0, 0, -1),
                size: this.options.bubbleSizeSeconday,
                color: this.options.colors.z,
                label: "-Z"
            }
        ];

        this.center = new Vector3(this.options.size / 2, this.options.size / 2, 0);
        this.selectedAxis = null;

        // All we need is a canvas
        this.innerHTML =
            "<canvas width='" +
            this.options.size +
            "' height='" +
            this.options.size +
            "'></canvas>";

        this.onMouseMove = this.onMouseMove.bind(this);
        this.onMouseOut = this.onMouseOut.bind(this);
        this.onMouseClick = this.onMouseClick.bind(this);
    }

    connectedCallback() {
        this.canvas = this.querySelector("canvas");
        this.context = this.canvas.getContext("2d");

        this.canvas.addEventListener("mousemove", this.onMouseMove, false);
        this.canvas.addEventListener("mouseout", this.onMouseOut, false);
        this.canvas.addEventListener("click", this.onMouseClick, false);
    }

    disconnectedCallback() {
        this.canvas.removeEventListener("mousemove", this.onMouseMove, false);
        this.canvas.removeEventListener("mouseout", this.onMouseOut, false);
        this.canvas.removeEventListener("click", this.onMouseClick, false);
    }

    onMouseMove(evt) {
        const rect = this.canvas.getBoundingClientRect();
        this.mouse = new Vector3(evt.clientX - rect.left, evt.clientY - rect.top, 0);
    }

    onMouseOut() {
        this.mouse = null;
    }

    onMouseClick() {
        if (!!this.onAxisSelected && typeof this.onAxisSelected == "function") {
            this.onAxisSelected({
                axis: this.selectedAxis.axis,
                direction: this.selectedAxis.direction.clone()
            });
        }
    }

    clear() {
        if (this.canvas) {
            this.context.clearRect(0, 0, this.canvas.width, this.canvas.height);
        }
    }

    drawCircle(p, radius = 10, color = "#FF0000") {
        this.context.beginPath();
        this.context.arc(p.x, p.y, radius, 0, 2 * Math.PI, false);
        this.context.fillStyle = color;
        this.context.fill();
        this.context.closePath();
    }

    drawLine(p1, p2, width = 1, color = "#FF0000") {
        this.context.beginPath();
        this.context.moveTo(p1.x, p1.y);
        this.context.lineTo(p2.x, p2.y);
        this.context.lineWidth = width;
        this.context.strokeStyle = color;
        this.context.stroke();
        this.context.closePath();
    }

    update() {
        this.clear();

        // Calculate the rotation matrix from the camera
        const rotMat = new Matrix4().makeRotationFromEuler(this.camera.rotation);
        const invRotMat = rotMat.clone().invert();

        for (const bubble of this.bubbles) {
            bubble.position = this.getBubblePosition(
                bubble.direction.clone().applyMatrix4(invRotMat)
            );
        }

        // Generate a list of layers to draw
        const layers = [];
        for (const axis in this.bubbles) {
            // Check if the name starts with a negative and dont add it to the layer list if secondary axis is turned off
            if (this.options.showSecondary === true || axis[0] !== "-") {
                layers.push(this.bubbles[axis]);
            }
        }

        // Sort the layers where the +Z position is last so its drawn on top of anything below it
        layers.sort((a, b) => (a.position.z > b.position.z ? 1 : -1));

        // If the mouse is over the gizmo, find the closest axis and highlight it
        this.selectedAxis = null;

        if (this.mouse) {
            let closestDist = Infinity;

            // Loop through each layer
            for (const bubble of layers) {
                const distance = this.mouse.distanceTo(bubble.position);

                // Only select the axis if its closer to the mouse than the previous or if its within its bubble circle
                if (distance < closestDist || distance < bubble.size) {
                    closestDist = distance;
                    this.selectedAxis = bubble;
                }
            }
        }

        // Draw the layers
        this.drawLayers(layers);
    }

    drawLayers(layers) {
        // For each layer, draw the bubble
        for (const bubble of layers) {
            let color = bubble.color;

            // Find the color
            if (this.selectedAxis === bubble) {
                color = "#FFFFFF";
            } else if (bubble.position.z >= -0.01) {
                color = bubble.color[0];
            } else {
                color = bubble.color[1];
            }

            // Draw the circle for the bubbble
            this.drawCircle(bubble.position, bubble.size, color);

            // Draw the line that connects it to the center if enabled
            if (bubble.line) {
                this.drawLine(this.center, bubble.position, bubble.line, color);
            }

            // Write the axis label (X,Y,Z) if provided
            if (bubble.label) {
                this.context.font = [
                    this.options.fontWeight,
                    this.options.fontSize,
                    this.options.fontFamily
                ].join(" ");
                this.context.fillStyle = this.options.fontColor;
                this.context.textBaseline = "middle";
                this.context.textAlign = "center";
                this.context.fillText(
                    bubble.label,
                    bubble.position.x,
                    bubble.position.y + this.options.fontYAdjust
                );
            }
        }
    }

    getBubblePosition(position) {
        return new Vector3(
            position.x *
                (this.center.x - this.options.bubbleSizePrimary / 2 - this.options.padding) +
                this.center.x,
            this.center.y -
                position.y *
                    (this.center.y - this.options.bubbleSizePrimary / 2 - this.options.padding),
            position.z
        );
    }
}