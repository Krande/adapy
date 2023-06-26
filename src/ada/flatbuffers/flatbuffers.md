# FlatBuffers

A library for encoding data in a space-efficient manner, useful for
serialization and communication.

To compile the flatbuffers schema files, use the following command:

````cmd
for /R %i in (*.fbs) do flatc --cpp --python -o ./compiled "%i"
````

Note! (Windows only) The above command will compile all the schema files in the
current directory and all subdirectories. If you want to compile only the
schema files in the current directory, use the following command:

````cmd
for %i in (*.fbs) do flatc --cpp --python -o ./compiled "%i"
````
