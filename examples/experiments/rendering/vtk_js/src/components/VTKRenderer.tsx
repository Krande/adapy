import React, {useEffect, useRef} from 'react';

// Load the rendering pieces we want to use (for both WebGL and WebGPU)
import 'vtk.js/Sources/Rendering/Profiles/Geometry';

import vtkFullScreenRenderWindow from 'vtk.js/Sources/Rendering/Misc/FullScreenRenderWindow';
import vtkActor from 'vtk.js/Sources/Rendering/Core/Actor';
import vtkMapper from 'vtk.js/Sources/Rendering/Core/Mapper';
import vtkConeSource from 'vtk.js/Sources/Filters/Sources/ConeSource';

function VTKRenderer() {
    const vtkContainerRef = useRef<HTMLDivElement | null>(null);
    const vtkContext = useRef<any | null>(null);

    useEffect(() => {
        if (!vtkContainerRef.current || vtkContext.current) {
            return;
        }

        const fullScreenRenderer = vtkFullScreenRenderWindow.newInstance({
            container: vtkContainerRef.current,
            controllerVisibility: true,
        });
        const renderer = fullScreenRenderer.getRenderer();
        const renderWindow = fullScreenRenderer.getRenderWindow();

        const coneSource = vtkConeSource.newInstance();
        const mapper = vtkMapper.newInstance();
        mapper.setInputConnection(coneSource.getOutputPort());

        const actor = vtkActor.newInstance();
        actor.setMapper(mapper);

        renderer.addActor(actor);
        renderer.resetCamera();
        renderWindow.render();

        vtkContext.current = {
            fullScreenRenderer,
            renderer,
            renderWindow,
            coneSource,
            actor,
            mapper,
        };

        return () => {
            if (vtkContext.current) {
                const {fullScreenRenderer, actor, mapper, coneSource} = vtkContext.current;
                actor.delete();
                mapper.delete();
                coneSource.delete();
                fullScreenRenderer.delete();
                vtkContext.current = null;
            }
        };
    }, []);

    return <div ref={vtkContainerRef}/>;
}

export default VTKRenderer;