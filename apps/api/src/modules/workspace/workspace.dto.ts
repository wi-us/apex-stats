import { IsBoolean, IsNumber, IsObject, IsOptional, IsString, Min } from "class-validator";

export class UpdateDbRowDto {
  @IsString()
  dbPath!: string;

  @IsString()
  table!: string;

  @IsNumber()
  rowId!: number;

  @IsObject()
  values!: Record<string, unknown>;
}

export class InsertDbRowDto {
  @IsString()
  dbPath!: string;

  @IsString()
  table!: string;

  @IsObject()
  values!: Record<string, unknown>;
}

export class DeleteDbRowDto {
  @IsString()
  dbPath!: string;

  @IsString()
  table!: string;

  @IsNumber()
  rowId!: number;
}

export class WriteFileDto {
  @IsString()
  path!: string;

  @IsString()
  content!: string;
}

export class CreateDirectoryDto {
  @IsString()
  path!: string;
}

export class MovePathDto {
  @IsString()
  from!: string;

  @IsString()
  to!: string;
}

export class DeletePathDto {
  @IsString()
  path!: string;

  @IsOptional()
  @IsBoolean()
  recursive?: boolean;
}

export class DbRowsQueryDto {
  @IsString()
  dbPath!: string;

  @IsString()
  table!: string;

  @IsOptional()
  @IsNumber()
  @Min(1)
  limit?: number;

  @IsOptional()
  @IsNumber()
  @Min(0)
  offset?: number;
}

