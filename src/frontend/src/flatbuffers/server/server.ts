// automatically generated by the FlatBuffers compiler, do not modify

/* eslint-disable @typescript-eslint/no-unused-vars, @typescript-eslint/no-explicit-any, @typescript-eslint/no-non-null-assertion */

import * as flatbuffers from 'flatbuffers';

import { FileObject, FileObjectT } from '../base/file-object.js';


export class Server implements flatbuffers.IUnpackableObject<ServerT> {
  bb: flatbuffers.ByteBuffer|null = null;
  bb_pos = 0;
  __init(i:number, bb:flatbuffers.ByteBuffer):Server {
  this.bb_pos = i;
  this.bb = bb;
  return this;
}

static getRootAsServer(bb:flatbuffers.ByteBuffer, obj?:Server):Server {
  return (obj || new Server()).__init(bb.readInt32(bb.position()) + bb.position(), bb);
}

static getSizePrefixedRootAsServer(bb:flatbuffers.ByteBuffer, obj?:Server):Server {
  bb.setPosition(bb.position() + flatbuffers.SIZE_PREFIX_LENGTH);
  return (obj || new Server()).__init(bb.readInt32(bb.position()) + bb.position(), bb);
}

newFileObject(obj?:FileObject):FileObject|null {
  const offset = this.bb!.__offset(this.bb_pos, 4);
  return offset ? (obj || new FileObject()).__init(this.bb!.__indirect(this.bb_pos + offset), this.bb!) : null;
}

allFileObjects(index: number, obj?:FileObject):FileObject|null {
  const offset = this.bb!.__offset(this.bb_pos, 6);
  return offset ? (obj || new FileObject()).__init(this.bb!.__indirect(this.bb!.__vector(this.bb_pos + offset) + index * 4), this.bb!) : null;
}

allFileObjectsLength():number {
  const offset = this.bb!.__offset(this.bb_pos, 6);
  return offset ? this.bb!.__vector_len(this.bb_pos + offset) : 0;
}

getFileObjectByName():string|null
getFileObjectByName(optionalEncoding:flatbuffers.Encoding):string|Uint8Array|null
getFileObjectByName(optionalEncoding?:any):string|Uint8Array|null {
  const offset = this.bb!.__offset(this.bb_pos, 8);
  return offset ? this.bb!.__string(this.bb_pos + offset, optionalEncoding) : null;
}

getFileObjectByPath():string|null
getFileObjectByPath(optionalEncoding:flatbuffers.Encoding):string|Uint8Array|null
getFileObjectByPath(optionalEncoding?:any):string|Uint8Array|null {
  const offset = this.bb!.__offset(this.bb_pos, 10);
  return offset ? this.bb!.__string(this.bb_pos + offset, optionalEncoding) : null;
}

deleteFileObject(obj?:FileObject):FileObject|null {
  const offset = this.bb!.__offset(this.bb_pos, 12);
  return offset ? (obj || new FileObject()).__init(this.bb!.__indirect(this.bb_pos + offset), this.bb!) : null;
}

startFileInLocalApp(obj?:FileObject):FileObject|null {
  const offset = this.bb!.__offset(this.bb_pos, 14);
  return offset ? (obj || new FileObject()).__init(this.bb!.__indirect(this.bb_pos + offset), this.bb!) : null;
}

static startServer(builder:flatbuffers.Builder) {
  builder.startObject(6);
}

static addNewFileObject(builder:flatbuffers.Builder, newFileObjectOffset:flatbuffers.Offset) {
  builder.addFieldOffset(0, newFileObjectOffset, 0);
}

static addAllFileObjects(builder:flatbuffers.Builder, allFileObjectsOffset:flatbuffers.Offset) {
  builder.addFieldOffset(1, allFileObjectsOffset, 0);
}

static createAllFileObjectsVector(builder:flatbuffers.Builder, data:flatbuffers.Offset[]):flatbuffers.Offset {
  builder.startVector(4, data.length, 4);
  for (let i = data.length - 1; i >= 0; i--) {
    builder.addOffset(data[i]!);
  }
  return builder.endVector();
}

static startAllFileObjectsVector(builder:flatbuffers.Builder, numElems:number) {
  builder.startVector(4, numElems, 4);
}

static addGetFileObjectByName(builder:flatbuffers.Builder, getFileObjectByNameOffset:flatbuffers.Offset) {
  builder.addFieldOffset(2, getFileObjectByNameOffset, 0);
}

static addGetFileObjectByPath(builder:flatbuffers.Builder, getFileObjectByPathOffset:flatbuffers.Offset) {
  builder.addFieldOffset(3, getFileObjectByPathOffset, 0);
}

static addDeleteFileObject(builder:flatbuffers.Builder, deleteFileObjectOffset:flatbuffers.Offset) {
  builder.addFieldOffset(4, deleteFileObjectOffset, 0);
}

static addStartFileInLocalApp(builder:flatbuffers.Builder, startFileInLocalAppOffset:flatbuffers.Offset) {
  builder.addFieldOffset(5, startFileInLocalAppOffset, 0);
}

static endServer(builder:flatbuffers.Builder):flatbuffers.Offset {
  const offset = builder.endObject();
  return offset;
}


unpack(): ServerT {
  return new ServerT(
    (this.newFileObject() !== null ? this.newFileObject()!.unpack() : null),
    this.bb!.createObjList<FileObject, FileObjectT>(this.allFileObjects.bind(this), this.allFileObjectsLength()),
    this.getFileObjectByName(),
    this.getFileObjectByPath(),
    (this.deleteFileObject() !== null ? this.deleteFileObject()!.unpack() : null),
    (this.startFileInLocalApp() !== null ? this.startFileInLocalApp()!.unpack() : null)
  );
}


unpackTo(_o: ServerT): void {
  _o.newFileObject = (this.newFileObject() !== null ? this.newFileObject()!.unpack() : null);
  _o.allFileObjects = this.bb!.createObjList<FileObject, FileObjectT>(this.allFileObjects.bind(this), this.allFileObjectsLength());
  _o.getFileObjectByName = this.getFileObjectByName();
  _o.getFileObjectByPath = this.getFileObjectByPath();
  _o.deleteFileObject = (this.deleteFileObject() !== null ? this.deleteFileObject()!.unpack() : null);
  _o.startFileInLocalApp = (this.startFileInLocalApp() !== null ? this.startFileInLocalApp()!.unpack() : null);
}
}

export class ServerT implements flatbuffers.IGeneratedObject {
constructor(
  public newFileObject: FileObjectT|null = null,
  public allFileObjects: (FileObjectT)[] = [],
  public getFileObjectByName: string|Uint8Array|null = null,
  public getFileObjectByPath: string|Uint8Array|null = null,
  public deleteFileObject: FileObjectT|null = null,
  public startFileInLocalApp: FileObjectT|null = null
){}


pack(builder:flatbuffers.Builder): flatbuffers.Offset {
  const newFileObject = (this.newFileObject !== null ? this.newFileObject!.pack(builder) : 0);
  const allFileObjects = Server.createAllFileObjectsVector(builder, builder.createObjectOffsetList(this.allFileObjects));
  const getFileObjectByName = (this.getFileObjectByName !== null ? builder.createString(this.getFileObjectByName!) : 0);
  const getFileObjectByPath = (this.getFileObjectByPath !== null ? builder.createString(this.getFileObjectByPath!) : 0);
  const deleteFileObject = (this.deleteFileObject !== null ? this.deleteFileObject!.pack(builder) : 0);
  const startFileInLocalApp = (this.startFileInLocalApp !== null ? this.startFileInLocalApp!.pack(builder) : 0);

  Server.startServer(builder);
  Server.addNewFileObject(builder, newFileObject);
  Server.addAllFileObjects(builder, allFileObjects);
  Server.addGetFileObjectByName(builder, getFileObjectByName);
  Server.addGetFileObjectByPath(builder, getFileObjectByPath);
  Server.addDeleteFileObject(builder, deleteFileObject);
  Server.addStartFileInLocalApp(builder, startFileInLocalApp);

  return Server.endServer(builder);
}
}
