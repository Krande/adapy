import { DOMWidgetModel, DOMWidgetView } from '@jupyter-widgets/base';
import React from 'react';
import { createRoot } from 'react-dom/client';
import App from './app'; // Your existing React app

export class JupyterReactWidgetModel extends DOMWidgetModel {
    defaults() {
        return {
            ...super.defaults(),
            _model_name: 'JupyterReactWidgetModel',
            _view_name: 'JupyterReactWidgetView',
            _model_module: 'jupyter-react-widget',
            _view_module: 'jupyter-react-widget',
            _model_module_version: '0.1.0',
            _view_module_version: '0.1.0',
        };
    }
}

export class JupyterReactWidgetView extends DOMWidgetView {
    render() {
        // Create a container div
        this.el.innerHTML = '<div id="jupyter-react-root"></div>';

        // Render the existing React app
        const container = document.getElementById('jupyter-react-root');
        if (container) {
            const root = createRoot(container);
            // root.render(<App />);
            root.render(React.createElement(App));
        }
    }
}
