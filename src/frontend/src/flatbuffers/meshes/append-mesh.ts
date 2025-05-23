// automatically generated by the FlatBuffers compiler, do not modify

/* eslint-disable @typescript-eslint/no-unused-vars, @typescript-eslint/no-explicit-any, @typescript-eslint/no-non-null-assertion */

import * as flatbuffers from 'flatbuffers';

import { Mesh, MeshT } from '../meshes/mesh.js';


export class AppendMesh implements flatbuffers.IUnpackableObject<AppendMeshT> {
  bb: flatbuffers.ByteBuffer|null = null;
  bb_pos = 0;
  __init(i:number, bb:flatbuffers.ByteBuffer):AppendMesh {
  this.bb_pos = i;
  this.bb = bb;
  return this;
}

static getRootAsAppendMesh(bb:flatbuffers.ByteBuffer, obj?:AppendMesh):AppendMesh {
  return (obj || new AppendMesh()).__init(bb.readInt32(bb.position()) + bb.position(), bb);
}

static getSizePrefixedRootAsAppendMesh(bb:flatbuffers.ByteBuffer, obj?:AppendMesh):AppendMesh {
  bb.setPosition(bb.position() + flatbuffers.SIZE_PREFIX_LENGTH);
  return (obj || new AppendMesh()).__init(bb.readInt32(bb.position()) + bb.position(), bb);
}

mesh(obj?:Mesh):Mesh|null {
  const offset = this.bb!.__offset(this.bb_pos, 4);
  return offset ? (obj || new Mesh()).__init(this.bb!.__indirect(this.bb_pos + offset), this.bb!) : null;
}

static startAppendMesh(builder:flatbuffers.Builder) {
  builder.startObject(1);
}

static addMesh(builder:flatbuffers.Builder, meshOffset:flatbuffers.Offset) {
  builder.addFieldOffset(0, meshOffset, 0);
}

static endAppendMesh(builder:flatbuffers.Builder):flatbuffers.Offset {
  const offset = builder.endObject();
  return offset;
}

static createAppendMesh(builder:flatbuffers.Builder, meshOffset:flatbuffers.Offset):flatbuffers.Offset {
  AppendMesh.startAppendMesh(builder);
  AppendMesh.addMesh(builder, meshOffset);
  return AppendMesh.endAppendMesh(builder);
}

unpack(): AppendMeshT {
  return new AppendMeshT(
    (this.mesh() !== null ? this.mesh()!.unpack() : null)
  );
}


unpackTo(_o: AppendMeshT): void {
  _o.mesh = (this.mesh() !== null ? this.mesh()!.unpack() : null);
}
}

export class AppendMeshT implements flatbuffers.IGeneratedObject {
constructor(
  public mesh: MeshT|null = null
){}


pack(builder:flatbuffers.Builder): flatbuffers.Offset {
  const mesh = (this.mesh !== null ? this.mesh!.pack(builder) : 0);

  return AppendMesh.createAppendMesh(builder,
    mesh
  );
}
}
