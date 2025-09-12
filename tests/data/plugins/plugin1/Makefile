# Define all zip targets
ZIPS = plugin1-v1.0.0.zip plugin1-v2.0.0.zip plugin1-v3.0.0.zip plugin1-v4.0.0.zip

.PHONY: all clean

all: $(ZIPS)

plugin1-v1.0.0.zip: src-v1/*
	zip -r plugin1-v1.0.0.zip src-v1/

plugin1-v2.0.0.zip: src-v2/*
	zip -r plugin1-v2.0.0.zip src-v2/

plugin1-v3.0.0.zip: src-v3/*
	zip -r plugin1-v3.0.0.zip src-v3/

plugin1-v4.0.0.zip: src-v4/*
	zip -r plugin1-v4.0.0.zip src-v4/

clean:
	rm -rf $(ZIPS)
