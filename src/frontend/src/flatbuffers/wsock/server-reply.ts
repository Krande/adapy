// automatically generated by the FlatBuffers compiler, do not modify

/* eslint-disable @typescript-eslint/no-unused-vars, @typescript-eslint/no-explicit-any, @typescript-eslint/no-non-null-assertion */

import * as flatbuffers from 'flatbuffers';

import { Error, ErrorT } from '../wsock/error.js';


export class ServerReply implements flatbuffers.IUnpackableObject<ServerReplyT> {
  bb: flatbuffers.ByteBuffer|null = null;
  bb_pos = 0;
  __init(i:number, bb:flatbuffers.ByteBuffer):ServerReply {
  this.bb_pos = i;
  this.bb = bb;
  return this;
}

static getRootAsServerReply(bb:flatbuffers.ByteBuffer, obj?:ServerReply):ServerReply {
  return (obj || new ServerReply()).__init(bb.readInt32(bb.position()) + bb.position(), bb);
}

static getSizePrefixedRootAsServerReply(bb:flatbuffers.ByteBuffer, obj?:ServerReply):ServerReply {
  bb.setPosition(bb.position() + flatbuffers.SIZE_PREFIX_LENGTH);
  return (obj || new ServerReply()).__init(bb.readInt32(bb.position()) + bb.position(), bb);
}

message():string|null
message(optionalEncoding:flatbuffers.Encoding):string|Uint8Array|null
message(optionalEncoding?:any):string|Uint8Array|null {
  const offset = this.bb!.__offset(this.bb_pos, 4);
  return offset ? this.bb!.__string(this.bb_pos + offset, optionalEncoding) : null;
}

error(obj?:Error):Error|null {
  const offset = this.bb!.__offset(this.bb_pos, 6);
  return offset ? (obj || new Error()).__init(this.bb!.__indirect(this.bb_pos + offset), this.bb!) : null;
}

static startServerReply(builder:flatbuffers.Builder) {
  builder.startObject(2);
}

static addMessage(builder:flatbuffers.Builder, messageOffset:flatbuffers.Offset) {
  builder.addFieldOffset(0, messageOffset, 0);
}

static addError(builder:flatbuffers.Builder, errorOffset:flatbuffers.Offset) {
  builder.addFieldOffset(1, errorOffset, 0);
}

static endServerReply(builder:flatbuffers.Builder):flatbuffers.Offset {
  const offset = builder.endObject();
  return offset;
}


unpack(): ServerReplyT {
  return new ServerReplyT(
    this.message(),
    (this.error() !== null ? this.error()!.unpack() : null)
  );
}


unpackTo(_o: ServerReplyT): void {
  _o.message = this.message();
  _o.error = (this.error() !== null ? this.error()!.unpack() : null);
}
}

export class ServerReplyT implements flatbuffers.IGeneratedObject {
constructor(
  public message: string|Uint8Array|null = null,
  public error: ErrorT|null = null
){}


pack(builder:flatbuffers.Builder): flatbuffers.Offset {
  const message = (this.message !== null ? builder.createString(this.message!) : 0);
  const error = (this.error !== null ? this.error!.pack(builder) : 0);

  ServerReply.startServerReply(builder);
  ServerReply.addMessage(builder, message);
  ServerReply.addError(builder, error);

  return ServerReply.endServerReply(builder);
}
}