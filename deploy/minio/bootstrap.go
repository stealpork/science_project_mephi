package main

import (
    "context"
    "fmt"
    "os"
    "time"

    madmin "github.com/minio/madmin-go/v3"
)

func main() {
    ctx, cancel := context.WithTimeout(context.Background(), 5*time.Minute)
    defer cancel()
    admin, err := madmin.New("minio:9000", os.Getenv("MINIO_ROOT_USER"), os.Getenv("MINIO_ROOT_PASSWORD"), false)
    if err != nil { panic(err) }
    for {
        _, err = admin.ServerInfo(ctx)
        if err == nil { break }
        select { case <-ctx.Done(): panic("MinIO startup timed out"); case <-time.After(time.Second): }
    }
    err = admin.AddUser(ctx, os.Getenv("S3_ACCESS_KEY_ID"), os.Getenv("S3_SECRET_ACCESS_KEY"))
    if err != nil { panic(err) }
    policy := []byte(`{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":["s3:ListBucket","s3:ListBucketVersions","s3:GetBucketLocation","s3:GetBucketVersioning","s3:PutBucketVersioning","s3:CreateBucket","s3:PutLifecycleConfiguration","s3:GetLifecycleConfiguration"],"Resource":["arn:aws:s3:::science-quarantine","arn:aws:s3:::science-files"]},{"Effect":"Allow","Action":["s3:GetObject","s3:GetObjectVersion","s3:PutObject","s3:DeleteObject","s3:DeleteObjectVersion"],"Resource":["arn:aws:s3:::science-quarantine/*","arn:aws:s3:::science-files/*"]}]}`)
    if err = admin.AddCannedPolicy(ctx, "science-media", policy); err != nil { panic(err) }
    if err = admin.SetPolicy(ctx, "science-media", os.Getenv("S3_ACCESS_KEY_ID"), false); err != nil { panic(err) }
    fmt.Println("MinIO application user and scoped policy configured")
}
