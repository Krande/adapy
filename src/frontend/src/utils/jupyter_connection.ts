// Not tested yet.

export function connect_to_jupyter() {
    // Create a connection to the Jupyter server
    const Jupyter = require('base/js/namespace');

    const my_comm = Jupyter.notebook.kernel.comm_manager.new_comm('my_comm_target', {foo: 'baz'});

    // Send a message to the Python side
    my_comm.send({hello: 'world'});
}
