// eslint-disable-next-line @typescript-eslint/no-var-requires
import { TypeChecker } from "esbuild-helpers";

const frontend = TypeChecker({
    basePath: "./",
    name: "typechecker",
    shortenFilenames: false,
    tsConfig: "./tsconfig.json"
});

frontend.printSettings();
frontend.inspectAndPrint();
