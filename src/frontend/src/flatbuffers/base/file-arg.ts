// automatically generated by the FlatBuffers compiler, do not modify

/* eslint-disable @typescript-eslint/no-unused-vars, @typescript-eslint/no-explicit-any, @typescript-eslint/no-non-null-assertion */

import * as flatbuffers from 'flatbuffers';

import { FileType } from '../base/file-type.js';


export class FileArg implements flatbuffers.IUnpackableObject<FileArgT> {
  bb: flatbuffers.ByteBuffer|null = null;
  bb_pos = 0;
  __init(i:number, bb:flatbuffers.ByteBuffer):FileArg {
  this.bb_pos = i;
  this.bb = bb;
  return this;
}

static getRootAsFileArg(bb:flatbuffers.ByteBuffer, obj?:FileArg):FileArg {
  return (obj || new FileArg()).__init(bb.readInt32(bb.position()) + bb.position(), bb);
}

static getSizePrefixedRootAsFileArg(bb:flatbuffers.ByteBuffer, obj?:FileArg):FileArg {
  bb.setPosition(bb.position() + flatbuffers.SIZE_PREFIX_LENGTH);
  return (obj || new FileArg()).__init(bb.readInt32(bb.position()) + bb.position(), bb);
}

argName():string|null
argName(optionalEncoding:flatbuffers.Encoding):string|Uint8Array|null
argName(optionalEncoding?:any):string|Uint8Array|null {
  const offset = this.bb!.__offset(this.bb_pos, 4);
  return offset ? this.bb!.__string(this.bb_pos + offset, optionalEncoding) : null;
}

fileType():FileType {
  const offset = this.bb!.__offset(this.bb_pos, 6);
  return offset ? this.bb!.readInt8(this.bb_pos + offset) : FileType.IFC;
}

static startFileArg(builder:flatbuffers.Builder) {
  builder.startObject(2);
}

static addArgName(builder:flatbuffers.Builder, argNameOffset:flatbuffers.Offset) {
  builder.addFieldOffset(0, argNameOffset, 0);
}

static addFileType(builder:flatbuffers.Builder, fileType:FileType) {
  builder.addFieldInt8(1, fileType, FileType.IFC);
}

static endFileArg(builder:flatbuffers.Builder):flatbuffers.Offset {
  const offset = builder.endObject();
  return offset;
}

static createFileArg(builder:flatbuffers.Builder, argNameOffset:flatbuffers.Offset, fileType:FileType):flatbuffers.Offset {
  FileArg.startFileArg(builder);
  FileArg.addArgName(builder, argNameOffset);
  FileArg.addFileType(builder, fileType);
  return FileArg.endFileArg(builder);
}

unpack(): FileArgT {
  return new FileArgT(
    this.argName(),
    this.fileType()
  );
}


unpackTo(_o: FileArgT): void {
  _o.argName = this.argName();
  _o.fileType = this.fileType();
}
}

export class FileArgT implements flatbuffers.IGeneratedObject {
constructor(
  public argName: string|Uint8Array|null = null,
  public fileType: FileType = FileType.IFC
){}


pack(builder:flatbuffers.Builder): flatbuffers.Offset {
  const argName = (this.argName !== null ? builder.createString(this.argName!) : 0);

  return FileArg.createFileArg(builder,
    argName,
    this.fileType
  );
}
}
