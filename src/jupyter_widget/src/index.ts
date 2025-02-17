import {
  JupyterFrontEnd,
  JupyterFrontEndPlugin
} from '@jupyterlab/application';

/**
 * Initialization data for the adapy_viewer_widget extension.
 */
const plugin: JupyterFrontEndPlugin<void> = {
  id: 'adapy_viewer_widget:plugin',
  description: 'A JupyterLab extension.',
  autoStart: true,
  activate: (app: JupyterFrontEnd) => {
    console.log('JupyterLab extension adapy_viewer_widget is activated!');
  }
};

export default plugin;
