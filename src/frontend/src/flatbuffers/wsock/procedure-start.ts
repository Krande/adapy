// automatically generated by the FlatBuffers compiler, do not modify

/* eslint-disable @typescript-eslint/no-unused-vars, @typescript-eslint/no-explicit-any, @typescript-eslint/no-non-null-assertion */

import * as flatbuffers from 'flatbuffers';

import { Parameter, ParameterT } from '../wsock/parameter.js';


export class ProcedureStart implements flatbuffers.IUnpackableObject<ProcedureStartT> {
  bb: flatbuffers.ByteBuffer|null = null;
  bb_pos = 0;
  __init(i:number, bb:flatbuffers.ByteBuffer):ProcedureStart {
  this.bb_pos = i;
  this.bb = bb;
  return this;
}

static getRootAsProcedureStart(bb:flatbuffers.ByteBuffer, obj?:ProcedureStart):ProcedureStart {
  return (obj || new ProcedureStart()).__init(bb.readInt32(bb.position()) + bb.position(), bb);
}

static getSizePrefixedRootAsProcedureStart(bb:flatbuffers.ByteBuffer, obj?:ProcedureStart):ProcedureStart {
  bb.setPosition(bb.position() + flatbuffers.SIZE_PREFIX_LENGTH);
  return (obj || new ProcedureStart()).__init(bb.readInt32(bb.position()) + bb.position(), bb);
}

procedureName():string|null
procedureName(optionalEncoding:flatbuffers.Encoding):string|Uint8Array|null
procedureName(optionalEncoding?:any):string|Uint8Array|null {
  const offset = this.bb!.__offset(this.bb_pos, 4);
  return offset ? this.bb!.__string(this.bb_pos + offset, optionalEncoding) : null;
}

parameters(index: number, obj?:Parameter):Parameter|null {
  const offset = this.bb!.__offset(this.bb_pos, 6);
  return offset ? (obj || new Parameter()).__init(this.bb!.__indirect(this.bb!.__vector(this.bb_pos + offset) + index * 4), this.bb!) : null;
}

parametersLength():number {
  const offset = this.bb!.__offset(this.bb_pos, 6);
  return offset ? this.bb!.__vector_len(this.bb_pos + offset) : 0;
}

static startProcedureStart(builder:flatbuffers.Builder) {
  builder.startObject(2);
}

static addProcedureName(builder:flatbuffers.Builder, procedureNameOffset:flatbuffers.Offset) {
  builder.addFieldOffset(0, procedureNameOffset, 0);
}

static addParameters(builder:flatbuffers.Builder, parametersOffset:flatbuffers.Offset) {
  builder.addFieldOffset(1, parametersOffset, 0);
}

static createParametersVector(builder:flatbuffers.Builder, data:flatbuffers.Offset[]):flatbuffers.Offset {
  builder.startVector(4, data.length, 4);
  for (let i = data.length - 1; i >= 0; i--) {
    builder.addOffset(data[i]!);
  }
  return builder.endVector();
}

static startParametersVector(builder:flatbuffers.Builder, numElems:number) {
  builder.startVector(4, numElems, 4);
}

static endProcedureStart(builder:flatbuffers.Builder):flatbuffers.Offset {
  const offset = builder.endObject();
  return offset;
}

static createProcedureStart(builder:flatbuffers.Builder, procedureNameOffset:flatbuffers.Offset, parametersOffset:flatbuffers.Offset):flatbuffers.Offset {
  ProcedureStart.startProcedureStart(builder);
  ProcedureStart.addProcedureName(builder, procedureNameOffset);
  ProcedureStart.addParameters(builder, parametersOffset);
  return ProcedureStart.endProcedureStart(builder);
}

unpack(): ProcedureStartT {
  return new ProcedureStartT(
    this.procedureName(),
    this.bb!.createObjList<Parameter, ParameterT>(this.parameters.bind(this), this.parametersLength())
  );
}


unpackTo(_o: ProcedureStartT): void {
  _o.procedureName = this.procedureName();
  _o.parameters = this.bb!.createObjList<Parameter, ParameterT>(this.parameters.bind(this), this.parametersLength());
}
}

export class ProcedureStartT implements flatbuffers.IGeneratedObject {
constructor(
  public procedureName: string|Uint8Array|null = null,
  public parameters: (ParameterT)[] = []
){}


pack(builder:flatbuffers.Builder): flatbuffers.Offset {
  const procedureName = (this.procedureName !== null ? builder.createString(this.procedureName!) : 0);
  const parameters = ProcedureStart.createParametersVector(builder, builder.createObjectOffsetList(this.parameters));

  return ProcedureStart.createProcedureStart(builder,
    procedureName,
    parameters
  );
}
}