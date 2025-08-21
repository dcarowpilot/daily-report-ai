if st.button("Test audio bucket write"):
    test_path = f"test/{int(datetime.utcnow().timestamp())}_hello.txt"
    try:
        url = upload_bytes_to_bucket(
            BUCKET_AUDIO,
            test_path,
            b"hello audio bucket",
            "text/plain"
        )
        if url:
            st.success(f"✅ Audio bucket write OK: {url}")
        else:
            st.error("❌ Audio bucket write FAILED. Check bucket name & storage policies.")
    except Exception as e:
        st.error(f"❌ Exception during test upload: {e}")