if st.button("Test audio bucket write"):
    test_url = upload_bytes_to_bucket(
        BUCKET_AUDIO,
        f"test/{int(datetime.utcnow().timestamp())}_hello.txt",
        b"hello audio bucket",
        "text/plain"
    )
    st.write("Audio test URL:", test_url or "(failed)")